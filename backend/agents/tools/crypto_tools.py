"""加密货币（币安现货）工具 —— 只读情报/行情 + 下单（需二次确认 + 强制风控）。

## 安全（不可移除）

`place_crypto_order` 标 `requires_confirmation=True`：orchestrator 先中断让 Jason 逐笔
确认；**即便确认，执行路径仍无条件过 RiskManager.check_order**，风控不通过一律拒单，
LLM 无法绕过。这是 CLAUDE.md「每笔交易须人工审核，永不放开」的代码承载物之一。

风控 broker_info 按**币安真实余额**自建（`adapter.build_broker_info` 绑 A 股 ManualTrade
不能复用）。风控规则本身市场无关，五条硬规则直接复用。

## 只读工具无需 key

行情/情报/排雷类只读工具走公开接口，不需要 API key；账户/下单需 key，未配时诚实报错。
"""
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.widgets import (
    crypto_account_widget,
    crypto_analysis_widget,
    crypto_derivatives_widget,
    crypto_market_widget,
    crypto_screen_widget,
    metric_cards_widget,
)

# ──────────────────── 只读：市场大势 / 排雷 / 衍生品 ────────────────────

class CryptoSymbolArgs(BaseModel):
    symbol: str = Field(..., min_length=3, description="加密货币交易对，如 BTCUSDT.BN")


class NoArgs(BaseModel):
    """无参工具的空参数模型（契约要求域工具都声明 args_model）。"""
    pass


@tool(
    name="get_crypto_market",
    description="加密市场大势快照：恐慌贪婪指数 + BTC 主导率 + 总市值 + BTC 200日大势（牛/熊）。"
                "判断「现在能不能做多波段」的总闸，无需参数。",
    args_model=NoArgs, category="crypto", group="crypto",
)
def get_crypto_market() -> ToolEnvelope:
    from crypto_intel_engine import btc_regime, market_context
    ctx = market_context()
    regime = btc_regime()
    data = {"market_context": ctx, "btc_regime": regime}
    return ToolEnvelope(data=data, widget=crypto_market_widget(data))


@tool(
    name="screen_crypto",
    description="单币排雷体检：排雷分(0-100)+红旗（供应稀释/解锁悬顶/开发活跃/市值）。"
                "判断「这个币该不该碰」，不是择时。",
    args_model=CryptoSymbolArgs, category="crypto", group="crypto",
)
def screen_crypto(symbol: str) -> ToolEnvelope:
    from crypto_intel_engine import screen_coin
    r = screen_coin(symbol)
    if r.get("score") is None:
        return ToolEnvelope(business_result="negative",
                            message=f"排雷数据缺失：{'；'.join(r.get('flags') or ['未知'])}")
    return ToolEnvelope(data=r, widget=crypto_screen_widget(r))


@tool(
    name="get_crypto_derivatives",
    description="币安衍生品情绪：资金费率(多空谁付钱)+未平仓 OI+多空持仓比。现货波段择时探照灯。",
    args_model=CryptoSymbolArgs, category="crypto", group="crypto",
)
def get_crypto_derivatives(symbol: str) -> ToolEnvelope:
    from acquisition.markets import crypto_derivatives as deriv
    snap = deriv.get_derivatives_snapshot(symbol)
    return ToolEnvelope(data=snap, widget=crypto_derivatives_widget(snap))


# ──────────────────── 只读：一句话一张卡（币版驾驶舱）────────────────────

@tool(
    name="analyze_crypto",
    description="【问一个币的首选】对单个加密货币做完整分析，一次给全：当前价+近期走势、"
                "技术信号状态、综合评分(0-100)、买/卖/持有建议、入场价/止盈位/止损位/建议仓位，"
                "并给出简单原因。融合技术择时+币安衍生品+BTC大势三维，排雷层作否决闸。"
                "用户问「看看 BTC / XX 币怎么样 / 能不能买」时用这个。",
    args_model=CryptoSymbolArgs, category="crypto", group="crypto",
)
def analyze_crypto(symbol: str) -> ToolEnvelope:
    from crypto_intel_engine import analyze_crypto_symbol

    # 配了 key 才拿真实账户 → 才能算具体建议仓位/金额与当前持仓占比（否则只给百分比）
    broker_info = None
    current_pct = 0.0
    from acquisition.markets import binance_trade as bt
    if bt.has_credentials():
        try:
            from trading_engine.brokers.binance_broker import get_binance_broker
            broker = get_binance_broker()
            if broker.connect():
                broker_info = _crypto_broker_info(broker)
                total = broker_info.get("total_value") or 0.0
                pos = (broker_info.get("positions") or {}).get(symbol)
                if pos and total:
                    current_pct = round((pos.get("market_value") or 0.0) / total * 100, 1)
        except Exception as e:  # noqa: BLE001 — 账户拿不到不影响只读分析
            logger.warning(f"analyze_crypto 账户读取失败 {symbol}: {e}")
            broker_info = None

    r = analyze_crypto_symbol(symbol, broker_info=broker_info, current_position_pct=current_pct)
    if r.get("error"):
        return ToolEnvelope(business_result="negative", message=r["error"])
    _record_crypto_cockpit_decision(r)
    return ToolEnvelope(data=r, widget=crypto_analysis_widget(r))


def _record_crypto_cockpit_decision(r: dict) -> None:
    """币版驾驶舱评级入 DecisionLog（source=crypto_cockpit），供后验归因与置信度校准。

    对齐股票 `_record_cockpit_decision`：留痕失败绝不影响主流程。source 单列，crypto
    与股票各算各的胜率/校准（`get_calibration_factor("crypto_cockpit")` 读的就是这条）。
    """
    try:
        from crypto_intel_engine.scorer import CRYPTO_SCORER_VERSION
        from decision_log import record_decision

        sizing = r.get("suggested") or {}
        record_decision(
            source="crypto_cockpit", symbol=r.get("symbol"), name=r.get("base_asset"),
            action=r.get("recommendation"), recommendation=r.get("recommendation"),
            confidence=r.get("composite"),
            entry_price=(r.get("price") or {}).get("latest"),
            stop_loss=r.get("stop_loss"), take_profit=r.get("take_profit"),
            position_pct=r.get("suggested_position_pct"),
            model_id="rule:crypto_cockpit_scorer", prompt_version=CRYPTO_SCORER_VERSION,
            input_snapshot={"dimensions": r.get("dimensions"),
                            "weights_used": r.get("weights_used"),
                            "available_dimensions": r.get("available_dimensions"),
                            "dimension_coverage": r.get("dimension_coverage"),
                            "screen": r.get("screen"),
                            "data_quality": r.get("data_quality")},
            output_summary={"composite": r.get("composite"),
                            "raw_composite": r.get("raw_composite"),
                            "calibration_factor": r.get("calibration_factor"),
                            "adjustments": list(r.get("adjustments") or ()),
                            "suggested": sizing},
            risk_passed=bool(sizing.get("risk_passed")),
        )
    except Exception:  # noqa: BLE001 — 留痕不可影响主流程
        pass


# ──────────────────── 只读：币安账户（需 key）────────────────────

@tool(
    name="get_crypto_account",
    description="查币安现货账户：可用 USDT、持仓币种与市值。需要 .env 配好 API key。",
    args_model=NoArgs, category="crypto", group="crypto",
)
def get_crypto_account() -> ToolEnvelope:
    from acquisition.markets import binance_trade as bt
    if not bt.has_credentials():
        return ToolEnvelope(business_result="negative",
                            message="币安 API key 未配置（.env 的 BINANCE_API_KEY/SECRET），无法查账户")
    from trading_engine.brokers.binance_broker import get_binance_broker
    broker = get_binance_broker()
    if not broker.connect():
        return ToolEnvelope(business_result="negative", message="币安连接失败（检查 key 权限/网络）")
    acct = broker.get_account_info()
    positions = [{"symbol": p.symbol, "quantity": p.quantity,
                  "current_price": p.current_price, "market_value": round(p.market_value, 2)}
                 for p in broker.get_positions()]
    data = {"account": acct, "positions": positions}
    return ToolEnvelope(data=data, widget=crypto_account_widget(data))


# ──────────────────── 下单（需确认 + 风控 + key）────────────────────

def get_recent_crypto_closed_pnls(limit: int = 10) -> tuple[list[float], str | None]:
    """回放 crypto 成交台账（CryptoTrade），算最近 N 笔平仓盈亏（最新在前）+ 最近亏损日期。

    加权平均成本法逐 symbol 回放，镜像 A 股 `adapter.get_recent_closed_pnls`，
    数据源换成 `crypto_trades` —— crypto 与股票各算各的「连亏 3 次」streak，互不污染。
    喂给 RiskManager 的 ConsecutiveLossRule；台账空则返回 ([], None)（规则自动放行）。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoTrade
    session = get_session()
    try:
        trades = (session.query(CryptoTrade)
                  .order_by(CryptoTrade.trade_date.asc(), CryptoTrade.id.asc()).all())
        if not trades:
            return [], None
        positions: dict[str, dict[str, float]] = {}
        closed: list[dict[str, Any]] = []   # {"pnl": float, "date": str|None}
        for t in trades:
            pos = positions.setdefault(t.symbol,
                                       {"quantity": 0.0, "total_cost": 0.0, "avg_cost": 0.0})
            if t.side == "BUY":
                pos["total_cost"] += t.amount + (t.commission or 0)
                pos["quantity"] += t.quantity
                if pos["quantity"] > 0:
                    pos["avg_cost"] = pos["total_cost"] / pos["quantity"]
            elif t.side == "SELL":
                sell_revenue = t.amount - (t.commission or 0)
                pnl = sell_revenue - pos["avg_cost"] * t.quantity
                closed.append({"pnl": pnl,
                               "date": str(t.trade_date) if t.trade_date else None})
                pos["quantity"] -= t.quantity
                pos["total_cost"] = pos["avg_cost"] * pos["quantity"]
        closed.reverse()   # 最新在前
        pnl_list = [c["pnl"] for c in closed[:limit]]
        last_loss_date = next((c["date"] for c in closed if c["pnl"] < 0), None)
        return pnl_list, last_loss_date
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 平仓盈亏回放失败: {e}")
        return [], None
    finally:
        session.close()


def _crypto_broker_info(broker) -> dict[str, Any]:
    """按币安真实余额自建 RiskManager 所需 broker_info（不复用 A 股 adapter）。

    连亏字段（recent_closed_pnls/last_loss_date）从 crypto 独立成交台账回放而来，
    使 CLAUDE.md「连续亏损 3 次暂停」硬风控对 crypto 真正生效（不再硬编码空值）。
    """
    acct = broker.get_account_info()
    positions_map = {
        p.symbol: {"market_value": p.market_value, "avg_cost": p.avg_cost,
                   "current_price": p.current_price}
        for p in broker.get_positions()
    }
    recent_pnls, last_loss_date = get_recent_crypto_closed_pnls(limit=10)
    return {
        "cash": acct["cash"], "market_value": acct["market_value"],
        "total_value": acct["total_value"], "unrealized_pnl": acct["unrealized_pnl"],
        "positions": positions_map,
        "recent_closed_pnls": recent_pnls, "last_loss_date": last_loss_date,
    }


def _risk_check(symbol: str, action: str, quantity: float, price: float,
                broker_info: dict) -> tuple[bool, list[str], list[str]]:
    from trading_engine.risk.adapter import get_effective_risk_config
    from trading_engine.risk.manager import RiskManager
    rm = RiskManager(get_effective_risk_config())
    passed, results = rm.check_order(symbol=symbol, action=action,
                                     quantity=quantity, price=price, broker_info=broker_info)
    return passed, [r.message for r in results], [r.message for r in results if not r.passed]


def _resolve_qty_price(symbol: str, side: str, quantity: float | None,
                       quote_amount: float | None, price: float | None,
                       broker) -> tuple[float, float]:
    """确定下单币量与参考价。quote_amount(USDT) 给了则按现价换算成币量。"""
    ref = price or broker.get_current_price(symbol)
    if quantity:
        return float(quantity), ref
    if quote_amount and ref:
        return float(quote_amount) / ref, ref
    return 0.0, ref


# ──────────────────── 资金腾挪：买入自动补足 + 卖后理财扫归 ────────────────────

_STABLE_QUOTE = "USDT"     # v1 只处理 USDT 计价对
_FUND_BUFFER = 1.003       # 补足现货时多留 0.3% 缓冲（覆盖费率/滑点微差）


def _auto_earn_enabled() -> bool:
    import os
    return os.getenv("CRYPTO_AUTO_EARN_ENABLED", "true").lower() not in ("false", "0", "off")


def _earn_dust_min() -> float:
    import os
    try:
        return float(os.getenv("CRYPTO_EARN_DUST_MIN", "1"))
    except ValueError:
        return 1.0


def _earn_assets() -> list[str]:
    import os
    raw = os.getenv("CRYPTO_EARN_ASSETS", "USDT")
    return [a.strip().upper() for a in raw.split(",") if a.strip()]


def _plan_funding(acct: dict, need_usdt: float) -> tuple[list[dict], float]:
    """现货 USDT 不够时，规划从「活期→资金」按序补足的步骤。返回 (steps, 仍缺口)。

    优先赎回活期（我们自己扫进去的、更快），再划转资金钱包。缺口>0 = 补不满。
    """
    spot = float(acct.get("spot_cash") or 0.0)
    remaining = round(max(0.0, need_usdt - spot), 2)
    steps: list[dict] = []
    if remaining <= 0:
        return steps, 0.0
    redeemable = float(acct.get("redeemable_cash") or 0.0)
    if remaining > 0 and redeemable > 0:
        amt = round(min(remaining, redeemable), 2)
        steps.append({"action": "redeem", "from": "理财活期", "asset": _STABLE_QUOTE, "amount": amt})
        remaining = round(remaining - amt, 2)
    transferable = float(acct.get("transferable_cash") or 0.0)
    if remaining > 0 and transferable > 0:
        amt = round(min(remaining, transferable), 2)
        steps.append({"action": "transfer", "from": "资金钱包", "asset": _STABLE_QUOTE, "amount": amt})
        remaining = round(remaining - amt, 2)
    return steps, round(max(0.0, remaining), 2)


def _execute_funding(broker, steps: list[dict], need_usdt: float) -> bool:
    """执行补足步骤（赎回/划转），到账有延迟 → 轮询最多 3 次确认现货够了才算成功。"""
    import time

    from acquisition.markets import binance_trade as bt
    for st in steps:
        ok = (broker.earn_redeem_flexible(st["asset"], st["amount"]) if st["action"] == "redeem"
              else broker.transfer_funding_to_spot(st["asset"], st["amount"]))
        if not ok:
            logger.error(f"补足现货步骤失败: {st}")
            return False
    # 轮询现货 USDT 到账（币安划转/赎回有秒级延迟）
    for _ in range(3):
        time.sleep(1.2)
        try:
            free = next((float(b.get("free", 0)) for b in bt.account().get("balances", [])
                         if b.get("asset") == _STABLE_QUOTE), 0.0)
        except Exception:  # noqa: BLE001
            free = 0.0
        if free >= need_usdt:
            return True
    logger.warning(f"补足后现货 {_STABLE_QUOTE} 仍不足 {need_usdt}")
    return False


def _auto_sweep_to_earn(broker) -> None:
    """卖出成交后：把现货**闲置稳定币**全自动申购最优活期理财吃收益（不弹确认）。

    Jason 批准全自动（活期可秒赎回、风险低）。留痕但**不进 CryptoTrade 台账、不算连亏
    streak**（申赎不是买卖交易）。失败仅日志，绝不回滚已成交的卖单。
    """
    if not _auto_earn_enabled():
        return
    dust = _earn_dust_min()
    try:
        acct = broker.get_account_info()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"扫归读账户失败: {e}")
        return
    # 现货可用稳定币（spot_cash 只含稳定币 free，正是卖出到账的闲置资金）
    idle = float(acct.get("spot_cash") or 0.0)
    if idle < dust:
        return
    for asset in _earn_assets():
        if asset != _STABLE_QUOTE:      # v1 仅扫 USDT；spot_cash 未按币种拆分
            continue
        amount = round(idle, 2)
        if amount < dust:
            continue
        if broker.earn_subscribe_flexible(asset, amount):
            _record_earn_sweep(asset, amount)


def _record_earn_sweep(asset: str, amount: float) -> None:
    """理财扫归留痕：business_event + DecisionLog 备注。**非交易**，不进 CryptoTrade 台账。"""
    try:
        from business_events import EARN_SWEEP, publish_event
        publish_event(EARN_SWEEP, source="crypto",
                      title=f"闲置 {amount:g} {asset} 自动申购活期理财（吃收益）",
                      asset=asset, amount=amount)
    except Exception:  # noqa: BLE001
        pass
    try:
        from decision_log import record_decision
        record_decision(source="crypto_earn", symbol=f"{asset}.EARN", action="SUBSCRIBE",
                        executed=True, risk_passed=True,
                        output_text=f"闲置 {amount:g} {asset} 自动申购活期理财")
    except Exception:  # noqa: BLE001
        pass


def preview_crypto_order(args: dict) -> dict:
    """确认前预览：订单详情 + 风控预检 + **买入自动补足披露**（不执行）。"""
    from acquisition.markets import binance_trade as bt
    if not bt.has_credentials():
        return {"error": "币安 API key 未配置（.env），无法下单"}
    from trading_engine.brokers.binance_broker import get_binance_broker
    broker = get_binance_broker()
    if not broker.connect():
        return {"error": "币安连接失败（检查 key 权限/网络）"}

    symbol = (args.get("symbol") or "").strip()
    side = (args.get("side") or "").strip().lower()
    action = "BUY" if side in ("buy", "买", "买入") else "SELL"
    qty, ref = _resolve_qty_price(symbol, side, args.get("quantity"),
                                  args.get("quote_amount"), args.get("price"), broker)
    if not symbol or qty <= 0 or not ref:
        return {"error": "缺少交易对/数量，或拿不到价格"}
    broker_info = _crypto_broker_info(broker)
    passed, msgs, failed = _risk_check(symbol, action, qty, ref, broker_info)
    out = {
        "symbol": symbol, "action": action, "quantity": round(qty, 8),
        "price": round(ref, 4), "est_amount_usdt": round(ref * qty, 2),
        "cash_usdt": round(broker_info["cash"], 2), "broker": "币安现货（实盘）",
        "risk_passed": passed, "risk_checks": msgs, "risk_failed": failed,
    }
    # 买入自动补足披露：现货 USDT 不够 → 列出「先赎活期/划资金」再买（同一次确认里看到）
    if action == "BUY":
        acct = broker.get_account_info()
        need = round(ref * qty * _FUND_BUFFER, 2)
        steps, short = _plan_funding(acct, need)
        out["spot_cash_usdt"] = acct.get("spot_cash")
        if steps:
            out["needs_funding"] = steps
        if short > 0:
            out["funding_short_usdt"] = short   # 补不满：连活期+资金都不够
    return out


class PlaceCryptoOrderArgs(BaseModel):
    symbol: str = Field(..., min_length=3, description="交易对，如 BTCUSDT.BN")
    side: Literal["buy", "sell"] = Field(..., description="买入或卖出")
    quantity: float | None = Field(None, gt=0, description="下单币量（与 quote_amount 二选一）")
    quote_amount: float | None = Field(None, gt=0, description="花多少 USDT（买入时用，与 quantity 二选一）")
    price: float | None = Field(None, gt=0, description="可选限价；不填按现价市价单")


@tool(
    name="place_crypto_order",
    description="【下单·币安现货实盘】买入或卖出加密货币。会先让 Jason 二次确认，并强制通过风控才执行。"
                "用户明确要「买/卖某个币」时调用。真金白银，必经人工确认。",
    args_model=PlaceCryptoOrderArgs,
    category="crypto", group="core",
    requires_confirmation=True, preview_fn=preview_crypto_order,
)
def place_crypto_order(symbol: str, side: str, quantity=None,
                       quote_amount=None, price=None) -> ToolEnvelope:
    from acquisition.markets import binance_trade as bt
    if not bt.has_credentials():
        return ToolEnvelope(business_result="negative",
                            message="币安 API key 未配置（.env 的 BINANCE_API_KEY/SECRET），无法下单")
    from trading_engine.brokers.base import OrderStatus
    from trading_engine.brokers.binance_broker import get_binance_broker
    broker = get_binance_broker()
    if not broker.connect():
        return ToolEnvelope(business_result="negative", message="币安连接失败（检查 key 权限/网络）")

    action = "BUY" if str(side).lower() in ("buy", "买", "买入") else "SELL"
    qty, ref = _resolve_qty_price(symbol, side, quantity, quote_amount, price, broker)
    if qty <= 0 or not ref:
        return ToolEnvelope(business_result="negative", message="下单失败：缺少数量或拿不到价格")

    # 强制风控（即便已确认也不可绕过）
    broker_info = _crypto_broker_info(broker)
    passed, msgs, failed = _risk_check(symbol, action, qty, ref, broker_info)
    if not passed:
        return ToolEnvelope(business_result="negative",
                            data={"executed": False, "reason": "风控未通过", "failed_rules": failed})

    # 买入自动补足现货：在已披露(preview)、已确认订单内执行「赎回活期/划转资金」
    if action == "BUY":
        acct = broker.get_account_info()
        need = round(ref * qty, 2)
        steps, short = _plan_funding(acct, round(need * _FUND_BUFFER, 2))
        if short > 0:
            return ToolEnvelope(business_result="negative",
                                data={"executed": False,
                                      "reason": f"可用买力不足，仍缺 {short} USDT（活期+资金也补不满）"})
        if steps and not _execute_funding(broker, steps, need):
            return ToolEnvelope(business_result="negative",
                                data={"executed": False,
                                      "reason": "自动补足现货失败（赎回/划转未及时到账），未下单"})

    order = broker.submit_order(symbol, action, qty, price)
    # 撤单/拒单/异常 → 失败
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FAILED):
        return ToolEnvelope(business_result="negative",
                            data={"executed": False, "reason": order.error_msg or "执行失败"})

    # 真实成交量以交易所回执为准，绝不用委托量兜底（否则挂单会被谎报成已成交）
    filled_qty = order.filled_quantity or 0.0
    if filled_qty <= 0:
        # 限价单挂在盘口、尚未成交 —— 已受理（affirmative）但**未成交**：不发成交事件、
        # 不落台账，executed=False/resting=True 把「挂单≠成交」如实传出去。
        _record_crypto_resting(symbol, action, price, order.order_id)
        return ToolEnvelope(
            data={"executed": False, "resting": True, "symbol": symbol, "action": action,
                  "order_id": order.order_id, "status": order.status.value,
                  "price": round(price, 4) if price else None, "quantity": qty},
            message=(f"限价单已挂出（order={order.order_id}），当前未成交，挂在盘口等待撮合。"
                     f"成交后才会计入台账与盈亏。"),
        )

    fill_price = order.filled_price or ref
    partial = filled_qty < qty
    _record_crypto_trade(symbol, action, fill_price, filled_qty, order.order_id,
                         commission=order.commission or 0.0)

    # 卖出成交 → 闲置 USDT 全自动扫进最优活期理财（吃收益，不弹确认）
    if action == "SELL":
        _auto_sweep_to_earn(broker)

    qty_label = f"{filled_qty:g}" + (f" / 委托 {qty:g}" if partial else "")
    widget = metric_cards_widget([
        {"label": "方向", "value": "买入" if action == "BUY" else "卖出", "type": "neutral"},
        {"label": "成交价", "value": f"${fill_price:,.4f}", "type": "neutral"},
        {"label": "数量", "value": qty_label, "type": "neutral"},
        {"label": "金额", "value": f"${fill_price * filled_qty:,.2f}", "type": "neutral"},
    ], title=f"✅ 币安已成交 · {symbol}" + ("（部分）" if partial else ""))
    return ToolEnvelope(
        data={"executed": True, "partial": partial, "symbol": symbol, "action": action,
              "order_id": order.order_id, "price": round(fill_price, 4),
              "quantity": filled_qty, "ordered_quantity": qty,
              "amount_usdt": round(fill_price * filled_qty, 2), "status": order.status.value},
        widget=widget,
    )


def _record_crypto_trade(symbol: str, action: str, price: float, qty: float,
                         order_id: str, commission: float = 0.0) -> None:
    """**真实成交**留痕：成交台账 + 业务事件 + DecisionLog(source=crypto)。吞异常不坏主流程。

    只在 filled_quantity > 0 时调用（qty 为已成交量）。台账（CryptoTrade）喂连亏风控回放；
    commission 取自币安 fills 汇总（手续费币种可能非 USDT，回放里作近似处理）。
    """
    # ① 成交台账（连亏风控的回放数据源）
    try:
        from datetime import date as _date

        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoTrade
        session = get_session()
        try:
            session.add(CryptoTrade(
                symbol=symbol, side=action, price=price, quantity=qty,
                amount=round(price * qty, 8), commission=commission or 0.0,
                order_id=order_id, trade_date=_date.today(),
            ))
            session.commit()
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 成交台账写入失败: {e}")
    # ② 业务事件
    try:
        from business_events import ORDER_FILLED, publish_event
        publish_event(ORDER_FILLED, source="crypto", symbol=symbol,
                      title=f"{'买入' if action == 'BUY' else '卖出'} {symbol} {qty:g} @ ${price:,.2f}（币安实盘）",
                      action=action, price=round(price, 4), quantity=qty,
                      amount=round(price * qty, 2))
    except Exception:  # noqa: BLE001
        pass
    # ③ 决策留痕
    try:
        from decision_log import record_decision
        record_decision(source="crypto", symbol=symbol, action=action,
                        entry_price=price, executed=True, risk_passed=True,
                        output_text=f"币安现货成交 order={order_id} {qty:g} @ ${price:,.4f}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 决策留痕失败: {e}")


def _record_crypto_resting(symbol: str, action: str, price: float | None, order_id: str) -> None:
    """限价单挂出（**未成交**）留痕：只写 DecisionLog(executed=False)。

    不发 ORDER_FILLED、不落成交台账 —— 挂单不是成交，成交后自然由 _record_crypto_trade 记。
    """
    try:
        from decision_log import record_decision
        record_decision(source="crypto", symbol=symbol, action=action,
                        entry_price=price, executed=False, risk_passed=True,
                        output_text=f"币安限价单挂出 order={order_id}（未成交，盘口等待撮合）")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 挂单留痕失败: {e}")
