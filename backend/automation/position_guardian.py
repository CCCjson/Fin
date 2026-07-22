"""
持仓守护常驻监控器 — 盘中自动盯持仓的止损/止盈/急跌/放量（开机自启）

与 price_alert_monitor 同款样板，但只对持仓票做定向批量行情
（fetch_quotes_by_symbols，单次轻量请求），不拉全市场快照、不耗代理翻页。
命中经 ws_manager 复用 type="risk_alert" 广播（前端 NotificationBanner 现成处理），
并同步进业务事件总线（ActivityFeed 可见）。
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from automation.trading_hours import _is_trading_hours
from common.market import A_SHARE
from common.market_time import market_today

# 轮询间隔（秒）
TRADING_INTERVAL = 30
IDLE_INTERVAL = 120
# 同一 (symbol, 触发类型) 的防抖窗口
DEBOUNCE_MINUTES = 30
# 急跌阈值：当日跌幅（%）/ 相邻两轮扫描间跌幅（%）
DAY_DROP_PCT = -5.0
RAPID_DROP_PCT = -1.5

# 模块级状态：上一轮价格（算轮间涨速）、防抖记录
_last_prices: Dict[str, float] = {}
_last_notified: Dict[Tuple[str, str], datetime] = {}
_last_scan_date: Optional[date] = None


def _risk_thresholds() -> Tuple[float, float]:
    """止损/止盈阈值（小数），与 /portfolio/risk-monitor 同源 RISK_CONFIG。"""
    from trading_engine.config import RISK_CONFIG
    return (RISK_CONFIG.get("stop_loss_pct", 0.05),
            RISK_CONFIG.get("take_profit_pct", 0.15))


def _held_positions() -> List[Dict[str, Any]]:
    from portfolio.calculator import PortfolioCalculator
    positions = PortfolioCalculator().get_current_positions()
    return [p for p in positions if (p.get("quantity") or 0) > 0]


def _prev_day_volumes(symbols: List[str]) -> Dict[str, float]:
    """每只持仓最近一个交易日的全天成交量（手，与东财快照 f5 同单位同源）。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import DailyQuote
    out: Dict[str, float] = {}
    session = get_session()
    try:
        for sym in symbols:
            row = (session.query(DailyQuote.volume)
                   .filter(DailyQuote.symbol == sym)
                   .order_by(DailyQuote.date.desc())
                   .first())
            if row and row[0]:
                out[sym] = float(row[0])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"持仓守护读取昨日成交量失败: {e}")
    finally:
        session.close()
    return out


def _debounced(symbol: str, kind: str, now: datetime) -> bool:
    last = _last_notified.get((symbol, kind))
    return bool(last and now - last < timedelta(minutes=DEBOUNCE_MINUTES))


def build_guard_status() -> Dict[str, Any]:
    """给 agent 工具用：每只持仓的实时价/当日盈亏/距止损止盈距离 + 风险级别。

    盘中用实时价现算；拿不到实时价的票回退持仓自带 EOD 值并标注。
    """
    held = _held_positions()
    if not held:
        return {"positions": [], "note": "当前无持仓"}

    from acquisition.markets.realtime import fetch_quotes_by_symbols
    quotes = {q["symbol"]: q for q in fetch_quotes_by_symbols([p["symbol"] for p in held])}
    stop_pct, tp_pct = _risk_thresholds()

    rows = []
    for p in held:
        sym = p["symbol"]
        q = quotes.get(sym) or {}
        price = q.get("price") or p.get("current_price")
        avg = p.get("avg_cost") or 0
        qty = p.get("quantity") or 0
        realtime = bool(q.get("price"))

        pnl_pct = round((price - avg) / avg * 100, 2) if price and avg > 0 else None
        prev_close = q.get("prev_close")
        day_pnl = (round((price - prev_close) * qty, 2)
                   if realtime and price and prev_close else None)

        level = "unknown"
        if pnl_pct is not None:
            ratio = pnl_pct / 100
            if ratio <= -stop_pct:
                level = "danger"       # 已触发止损
            elif ratio <= -stop_pct * 0.6:
                level = "warning"      # 接近止损
            elif ratio >= tp_pct:
                level = "take_profit"  # 已触发止盈
            elif ratio >= tp_pct * 0.8:
                level = "near_tp"
            else:
                level = "safe"

        rows.append({
            "symbol": sym,
            "name": p.get("name") or q.get("name") or sym,
            "quantity": qty,
            "avg_cost": avg,
            "price": price,
            "price_as_of": "realtime" if realtime else "eod",
            "day_change_pct": q.get("change_percent") if realtime else None,
            "day_pnl": day_pnl,
            "unrealized_pnl_pct": pnl_pct,
            "stop_loss_price": round(avg * (1 - stop_pct), 2) if avg > 0 else None,
            "take_profit_price": round(avg * (1 + tp_pct), 2) if avg > 0 else None,
            "dist_stop_loss": (round(pnl_pct + stop_pct * 100, 2)
                               if pnl_pct is not None else None),
            "dist_take_profit": (round(tp_pct * 100 - pnl_pct, 2)
                                 if pnl_pct is not None else None),
            "level": level,
        })

    level_order = {"danger": 0, "warning": 1, "take_profit": 2, "near_tp": 3,
                   "safe": 4, "unknown": 5}
    rows.sort(key=lambda r: (level_order.get(r["level"], 9),
                             -(abs(r["unrealized_pnl_pct"] or 0))))
    return {
        "positions": rows,
        "stop_loss_pct": -stop_pct * 100,
        "take_profit_pct": tp_pct * 100,
        "guard_running": _is_trading_hours(),
    }


def _scan_once() -> List[Dict[str, Any]]:
    """同步：定向拉持仓票实时行情，检查止损/止盈/急跌/放量，返回命中事件。"""
    global _last_scan_date
    today = market_today(A_SHARE)
    if _last_scan_date != today:
        # 跨天：清空轮间价格缓存，避免用昨天的最后一次价格算「短时急跌」，
        # 把正常的隔夜跳空误判成急跌
        _last_prices.clear()
        _last_scan_date = today

    held = _held_positions()
    if not held:
        return []

    from acquisition.markets.realtime import fetch_quotes_by_symbols
    symbols = [p["symbol"] for p in held]
    quotes = {q["symbol"]: q for q in fetch_quotes_by_symbols(symbols)}
    if not quotes:
        return []
    prev_vols = _prev_day_volumes(symbols)
    stop_pct, tp_pct = _risk_thresholds()

    now = datetime.now()
    events: List[Dict[str, Any]] = []

    def _hit(symbol: str, name: str, kind: str, message: str,
             severity: str, detail: Optional[Dict] = None):
        if _debounced(symbol, kind, now):
            return
        _last_notified[(symbol, kind)] = now
        events.append({
            "symbol": symbol, "name": name, "kind": kind,
            "message": message, "severity": severity, "detail": detail or {},
        })

    for p in held:
        sym = p["symbol"]
        q = quotes.get(sym)
        if not q or not q.get("price"):
            continue
        name = p.get("name") or q.get("name") or sym
        price = q["price"]
        avg = p.get("avg_cost") or 0
        day_pct = q.get("change_percent")

        # 1) 止损 / 接近止损 / 止盈（口径与 /portfolio/risk-monitor 一致）
        if avg > 0:
            pnl_ratio = (price - avg) / avg
            if pnl_ratio <= -stop_pct:
                _hit(sym, name, "stop_loss",
                     f"{name} 亏损 {abs(pnl_ratio) * 100:.2f}% 已跌破止损线 "
                     f"{stop_pct * 100:.0f}%（现价 {price}，成本 {avg:.2f}），建议按纪律止损",
                     "ERROR", {"pnl_pct": round(pnl_ratio * 100, 2), "price": price})
            elif pnl_ratio <= -stop_pct * 0.6:
                _hit(sym, name, "near_stop",
                     f"{name} 亏损 {abs(pnl_ratio) * 100:.2f}%，接近止损线 "
                     f"{stop_pct * 100:.0f}%（现价 {price}）",
                     "WARNING", {"pnl_pct": round(pnl_ratio * 100, 2), "price": price})
            elif pnl_ratio >= tp_pct:
                _hit(sym, name, "take_profit",
                     f"{name} 盈利 {pnl_ratio * 100:.2f}% 触发止盈线 "
                     f"{tp_pct * 100:.0f}%（现价 {price}），可考虑落袋",
                     "WARNING", {"pnl_pct": round(pnl_ratio * 100, 2), "price": price})

        # 2) 急跌异动：当日大跌，或相邻两轮（约 30s）内快速下挫
        last = _last_prices.get(sym)
        if day_pct is not None and day_pct <= DAY_DROP_PCT:
            _hit(sym, name, "sharp_drop",
                 f"{name} 当日大跌 {day_pct:.2f}%（现价 {price}），注意风险",
                 "WARNING", {"day_change_pct": day_pct, "price": price})
        elif last and last > 0:
            step_pct = (price - last) / last * 100
            if step_pct <= RAPID_DROP_PCT:
                _hit(sym, name, "rapid_drop",
                     f"{name} 短时急跌 {step_pct:.2f}%（{last} → {price}，约 "
                     f"{TRADING_INTERVAL}s 内），可能有突发",
                     "WARNING", {"step_pct": round(step_pct, 2), "price": price})

        # 3) 放量异动：今日累计量已超昨日全天（f5 与日线同为「手」，同源东财）
        vol, y_vol = q.get("volume"), prev_vols.get(sym)
        if vol and y_vol and vol > y_vol:
            _hit(sym, name, "volume_spike",
                 f"{name} 今日成交量已达昨日全天的 {vol / y_vol:.1f} 倍"
                 f"（当日{'跌' if (day_pct or 0) < 0 else '涨'} {abs(day_pct or 0):.2f}%），"
                 "出现放量异动",
                 "WARNING", {"volume_ratio_vs_yesterday": round(vol / y_vol, 2)})

        _last_prices[sym] = price

    return events


# `position_guard_loop` 常驻轮询已删（Jason 2026-07-10 拍板）：同 price_alert_monitor。
# 按需入口是 `build_guard_status()`（agents/tools/portfolio_tools.py 已在用），
# 命中告警的事件流由 `_scan_once()` 提供，MoneyBill 需要时自己调。
