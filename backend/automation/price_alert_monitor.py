"""
价格预警常驻监控器 — 开机自启的独立后台任务（不依赖 automation_scheduler）

盘中每隔 N 秒拉一次全市场快照，比对所有 active 预警，命中即通过
ws_manager 广播 type="price_alert"，前端复用 RiskToast 弹窗。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.repository import PriceAlertRepository

# 上一次扫描的覆盖情况。模块级是刻意的：`_scan_once` 的返回值形状被调用方依赖，
# 不想为了带出覆盖信息去改它的签名（`scan_with_coverage` 才是完整口径）。
_LAST_COVERAGE: Dict[str, Any] = {"checked": 0, "missing": [], "skipped_closed": []}

# 轮询间隔（秒）
TRADING_INTERVAL = 30
IDLE_INTERVAL = 120
# 反复触发(repeat=1)的防抖窗口
DEBOUNCE_MINUTES = 30


def _check_alert(alert, quote: Dict) -> bool:
    """判断单条预警是否命中"""
    price = quote.get("price")
    change_pct = quote.get("change_pct")
    t = alert.threshold
    if alert.alert_type == "price_above":
        return price is not None and price >= t
    if alert.alert_type == "price_below":
        return price is not None and price <= t
    if alert.alert_type == "pct_change":
        if change_pct is None:
            return False
        return change_pct >= t if t >= 0 else change_pct <= t
    return False


def _scan_once() -> List[Dict]:
    """同步：定向拉行情 + 比对预警，返回命中事件列表（供广播）。在线程池执行。

    ⚠️ 覆盖情况（哪些票**根本没被检查**）通过 `last_coverage()` 取，见那里的注释。
    """
    return scan_with_coverage()["events"]


def last_coverage() -> Dict[str, Any]:
    """上一次扫描的覆盖情况 —— `{checked, missing, skipped_closed}`。

    🔴 **必须能穿过工具边界**（S6 §1.4）：只写 logger 的话，Jason 问「我的预警触发了吗」
    拿到的是「检查完毕，当前没有预警触发」，而实际可能是 5 条美股预警**一条都没被检查**。
    那正是 P1-4 记录的病换了一层楼。
    """
    return dict(_LAST_COVERAGE)


def scan_with_coverage() -> Dict[str, Any]:
    """`{events, checked, missing, skipped_closed}`。"""
    _LAST_COVERAGE.update({"checked": 0, "missing": [], "skipped_closed": []})
    session = get_session()
    try:
        repo = PriceAlertRepository(session)
        alerts = repo.get_active_alerts()
        if not alerts:
            return {"events": [], **_LAST_COVERAGE}

        # 定向拉预警涉及的票（单次 ulist 轻量请求）。原先拉全市场快照每轮要
        # 代理翻页数十次，是本监控被关闭开机自启的原因；改定向后成本可忽略。
        from acquisition.markets.quote_router import fetch_quotes_detailed
        symbols = sorted({a.symbol for a in alerts if a.symbol})
        detail = fetch_quotes_detailed(symbols)
        rows = detail["quotes"]
        for r in rows:
            r.setdefault("change_pct", r.get("change_percent"))
        quote_map = {r["symbol"]: r for r in rows if r.get("symbol")}
        # 🔴 **取不到价不能静默跳过**（S6）：此前 `quote_map.get()` 返 None 就
        # `continue`，于是港股/美股/crypto 的预警**不告警、不报错、不写日志** ——
        # 只有查库才知道它们根本没被检查过。
        # `skipped_closed` 是刻意不取（市场闭市），与「取失败」必须分开说。
        if detail["missing"]:
            logger.warning(
                f"价格预警：{len(detail['missing'])} 只取不到行情，本轮**没有被检查**"
                f"（{detail['missing'][:5]}）")
        if detail["skipped_closed"]:
            logger.debug(f"价格预警：{len(detail['skipped_closed'])} 只所在市场闭市，跳过")
        _LAST_COVERAGE.update({
            "checked": len(quote_map),
            "missing": detail["missing"],
            "skipped_closed": detail["skipped_closed"],
        })

        # 资金量：按真实总资金算建议买入（每轮构建一次复用）
        from trading_engine.risk.adapter import build_broker_info, get_total_capital
        from trading_engine.position_sizing import size_position
        total_capital = get_total_capital()
        broker_info = build_broker_info(total_capital)

        now = datetime.now()
        events: List[Dict] = []
        for alert in alerts:
            quote = quote_map.get(alert.symbol)
            if not quote:
                continue
            if not _check_alert(alert, quote):
                continue

            # 防抖：repeat=1 时同一预警 DEBOUNCE_MINUTES 内只推一次
            if alert.repeat == 1 and alert.last_notified_at:
                if now - alert.last_notified_at < timedelta(minutes=DEBOUNCE_MINUTES):
                    continue

            price = quote.get("price")
            msg = alert.message or f"{alert.name or alert.symbol} 触发预警：{alert.alert_type} {alert.threshold}（现价 {price}）"
            # 按真实资金给出这个价位的建议买入量（辅助信息，不影响触发）
            sizing = size_position(
                alert.symbol, price, 20.0,
                broker_info=broker_info, total_capital=total_capital,
            )
            events.append({
                "type": "price_alert",
                "data": {
                    "alert_id": alert.alert_id,
                    "symbol": alert.symbol,
                    "name": alert.name,
                    "alert_type": alert.alert_type,
                    "threshold": alert.threshold,
                    "price": price,
                    "change_pct": quote.get("change_pct"),
                    "message": msg,
                    "rule": "到价预警",
                    "severity": "WARNING",
                    "suggested": {
                        "shares": sizing["shares"],
                        "amount": sizing["amount"],
                        "affordable": sizing["affordable"],
                    },
                },
                "timestamp": now.isoformat(timespec="seconds"),
            })

            # 更新状态
            alert.triggered_at = now
            alert.triggered_price = price
            alert.last_notified_at = now
            if alert.repeat == 0:
                alert.status = "triggered"
        session.commit()
        return {"events": events, **_LAST_COVERAGE}
    except Exception as e:
        logger.warning(f"价格预警扫描异常: {e}")
        session.rollback()
        return {"events": [], **_LAST_COVERAGE}
    finally:
        session.close()


# `price_alert_loop` 常驻轮询已删（Jason 2026-07-10 拍板）：没有独立开关、
# FIN_DISABLE_SCHEDULERS 管不到，盘中每 30s 无条件出网。改由 MoneyBill 的
# `check_price_alerts` 工具调 `_scan_once()` 现拉。
