"""crypto 自主引擎护栏层 —— 免逐笔确认的代价就是这一整套硬护栏，颗颗可单测。

与 RiskManager 5 条硬风控**互补不替代**：风控管「这笔单本身合不合规」，护栏管「自主系统
整体别失控」（总开关/当日熔断/频次与费用漂移/单笔与单币上限/成本净边际）。每个函数返回
`(passed, reason)`：passed=True 放行，False 拦截并给人话原因。
"""
from loguru import logger

from crypto_intel_engine.dsl import CostModel, Guardrails, round_trip_cost

# ──────────────────── 全局 kill-switch（DB 持久化，跨 tick 即时生效）────────────────────

_KILL_KEY = "crypto_engine_killed"


def is_killed() -> bool:
    """总开关：UserSettings.crypto_engine_killed 为真 → 引擎整体停摆（任何策略都不评估/不下单）。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import UserSettings
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == _KILL_KEY).first()
        return bool(row and str(row.value).lower() in ("1", "true", "yes", "on"))
    except Exception:  # noqa: BLE001 — 读不到按「未 kill」放行，避免读库抖动误停；真要停用 enabled/env
        return False
    finally:
        session.close()


def set_killed(killed: bool) -> None:
    """翻转 kill-switch（API /engine/kill|unkill 调用）。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import UserSettings
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == _KILL_KEY).first()
        val = "1" if killed else "0"
        if row:
            row.value = val
        else:
            session.add(UserSettings(key=_KILL_KEY, value=val,
                                     description="crypto 自主引擎总开关（1=停摆）"))
        session.commit()
    finally:
        session.close()


# ──────────────────── 成本净边际闸（手续费+滑点，套利命门）────────────────────


def net_edge(gross_edge: float, cm: CostModel) -> float:
    """净边际 = 毛目标边际 - 往返成本（2×taker + 滑点）。"""
    return gross_edge - round_trip_cost(cm)


def effective_cost_model(cm: CostModel, symbol: str,
                         notional_usdt: float | None) -> tuple[CostModel, dict]:
    """把成本模型里的**滑点假设**换成当下真实盘口的**实测值**。

    `slippage_pct` 默认 0.05% 是个拍脑袋的数——薄盘小币真实滑点可能是它的十倍，
    厚盘 BTC 又远小于它。成本闸是「这笔到底赚不赚」的最后一道判断，喂它假数据等于白判。
    做法：按本单名义额扫真实单簿算 VWAP 偏离（`estimate_slippage`）。

    拿不到盘口 / 未开启 measured → **原样返回**并在 detail 里标 `source='fixed'`
    （不假装测过）。单簿被吃穿（`exhausted`）时额外标 `thin_book`，调用方应当心。
    """
    detail: dict = {"source": "fixed", "slippage_pct": cm.slippage_pct}
    if cm.slippage_source != "measured" or not notional_usdt or notional_usdt <= 0:
        return cm, detail
    try:
        from acquisition.markets.crypto import CryptoFetcher, estimate_slippage
        book = CryptoFetcher().get_order_book(symbol, limit=100)
        est = estimate_slippage(book, notional_usdt, "BUY")
    except Exception as e:  # noqa: BLE001 — 盘口取不到就用固定假设，不阻断决策
        logger.warning(f"盘口滑点实测失败 {symbol}: {e}")
        detail["error"] = str(e)
        return cm, detail

    if not est:
        return cm, detail
    measured = est["slippage_pct"]
    detail.update({
        "source": "measured", "slippage_pct": measured,
        "fixed_assumption": cm.slippage_pct,
        "thin_book": est.get("exhausted", False),
        "best_price": est.get("best_price"), "avg_price": est.get("avg_price"),
    })
    # 单簿被吃穿说明深度不够，实测值是**低估**的 → 取实测与固定假设的较大者，偏保守
    slip = max(measured, cm.slippage_pct) if est.get("exhausted") else measured
    detail["applied_slippage_pct"] = slip
    return cm.model_copy(update={"slippage_pct": slip}), detail


def cost_gate(gross_edge: float | None, cm: CostModel) -> tuple[bool, str, float | None]:
    """成本感知闸：毛边际扣完往返成本后，须 > 0 且 ≥ 配置的最小净边际，否则不下单。

    返回 (passed, reason, net)。gross_edge 取不到（None）视为不满足——宁可不做也不裸冲。
    """
    if gross_edge is None:
        return False, "拿不到目标毛边际（止盈位/入场价缺失），成本闸无法判定", None
    net = net_edge(gross_edge, cm)
    rt = round_trip_cost(cm)
    if net <= 0:
        return False, f"净边际{net:.4%}≤0（毛{gross_edge:.4%} 扣往返成本{rt:.4%}），手续费+滑点吃光", net
    if net < cm.min_net_edge_pct:
        return False, f"净边际{net:.4%} < 最小要求{cm.min_net_edge_pct:.4%}，不划算不下单", net
    return True, f"净边际{net:.4%} ≥ {cm.min_net_edge_pct:.4%}（毛{gross_edge:.4%}-成本{rt:.4%}）", net


def take_profit_clears_cost(entry: float | None, take_profit: float | None,
                            cm: CostModel) -> tuple[bool, str]:
    """止盈位本身必须 ≥ 入场×(1+往返成本+最小净边际)，否则止盈了也是白干。"""
    if not (entry and take_profit and entry > 0):
        return False, "入场价/止盈位缺失，无法校验止盈是否覆盖成本"
    need = entry * (1 + round_trip_cost(cm) + cm.min_net_edge_pct)
    if take_profit < need:
        return False, f"止盈{take_profit:.6g} < 覆盖成本所需{need:.6g}，不下单"
    return True, "止盈位覆盖往返成本+最小净边际"


# ──────────────────── 频次 / 费用 / 单笔 / 单币 / 当日熔断 ────────────────────


def check_daily_counts(orders_today: int, round_trips_today: int, fees_today: float,
                       g: Guardrails) -> tuple[bool, str]:
    """单日笔数 / 往返数 / 累计手续费上限（小额高频套利的费用漂移护栏）。"""
    if orders_today >= g.max_orders_per_day:
        return False, f"当日已下 {orders_today} 单，达上限 {g.max_orders_per_day}"
    if g.max_round_trips_per_day is not None and round_trips_today >= g.max_round_trips_per_day:
        return False, f"当日已 {round_trips_today} 次往返，达上限 {g.max_round_trips_per_day}"
    if g.max_fees_per_day_usdt is not None and fees_today >= g.max_fees_per_day_usdt:
        return False, f"当日手续费 ${fees_today:.2f} 达上限 ${g.max_fees_per_day_usdt:.2f}"
    return True, "频次/费用未触顶"


def check_notional_cap(notional_usdt: float, g: Guardrails) -> tuple[bool, str]:
    """单笔名义金额上限。"""
    if notional_usdt > g.per_order_notional_usdt:
        return False, f"单笔名义 ${notional_usdt:.2f} > 上限 ${g.per_order_notional_usdt:.2f}"
    return True, "单笔名义未超上限"


def check_symbol_exposure(symbol: str, new_notional_usdt: float, broker_info: dict,
                          cap_pct: float) -> tuple[bool, str]:
    """单币敞口（现有持仓市值 + 本次新增）/ 总资产 ≤ cap_pct。"""
    total = float((broker_info or {}).get("total_value") or 0.0)
    if total <= 0:
        return True, "总资产未知，跳过单币敞口校验（风控层仍会兜）"
    held = float(((broker_info.get("positions") or {}).get(symbol) or {}).get("market_value") or 0.0)
    exposure = (held + new_notional_usdt) / total
    if exposure > cap_pct:
        return False, f"单币敞口 {exposure:.1%} > 上限 {cap_pct:.1%}"
    return True, f"单币敞口 {exposure:.1%} ≤ {cap_pct:.1%}"


def check_whitelist(symbol: str, allowed: list[str]) -> tuple[bool, str]:
    """白名单双重卡（universe 之外再镜像一层，防配置疏漏）。空列表=不额外限制。"""
    if not allowed:
        return True, "无额外白名单限制"
    up = symbol.upper()
    if up not in {s.upper() for s in allowed}:
        return False, f"{symbol} 不在护栏白名单内"
    return True, "在白名单内"


def check_daily_loss(realized_today: float, unrealized: float, capital: float,
                     daily_loss_pct: float) -> tuple[bool, str]:
    """当日回撤熔断：已实现+未实现亏损达 capital×daily_loss_pct → 熔断（passed=False）。"""
    if capital <= 0:
        return True, "资金口径未知，当日熔断跳过（风控 MaxDailyLossRule 仍在）"
    pnl = realized_today + unrealized
    threshold = -abs(daily_loss_pct) * capital
    if pnl <= threshold:
        return False, f"当日盈亏 ${pnl:.2f} 触及熔断线 ${threshold:.2f}（{daily_loss_pct:.1%}）"
    return True, f"当日盈亏 ${pnl:.2f} 未触熔断线 ${threshold:.2f}"
