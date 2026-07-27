"""加密货币（币安现货）工具 —— 只读情报/行情 + 下单（需二次确认 + 强制风控）。

## 安全（不可移除）

`place_crypto_order` 标 `requires_confirmation=True`：orchestrator 先中断让 Jason 逐笔
确认；**即便确认，执行路径仍无条件过 RiskManager.check_order**，风控不通过一律拒单，
LLM 无法绕过。这是 CLAUDE.md「每笔交易须人工审核」的代码承载物之一（交互式聊天单
永不放开；crypto 自主策略引擎的范围豁免见 `crypto_strategy/`）。

风控 broker_info 按**币安真实余额**自建，执行/资金/台账原语现集中在
`crypto_intel_engine/execution.py`（**单一真源**），聊天下单与自主引擎共用同一份，
永不分叉。本模块下方以别名保留旧私有名，函数体零改动。

## 只读工具无需 key

行情/情报/排雷类只读工具走公开接口，不需要 API key；账户/下单需 key，未配时诚实报错。
"""
from typing import Literal

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
from common.trade_source import AI_ADVICE as TRADE_AI_ADVICE

# 执行/风控/资金/台账原语的单一真源（见 crypto_intel_engine/execution.py）。
# 以旧私有名别名导入，使本模块下方 preview/place 的函数体零改动，两条下单路径共用同一份。
from crypto_intel_engine.execution import (
    FUND_BUFFER as _FUND_BUFFER,
)
from crypto_intel_engine.execution import (
    auto_sweep_to_earn as _auto_sweep_to_earn,
)
from crypto_intel_engine.execution import (
    crypto_broker_info as _crypto_broker_info,
)
from crypto_intel_engine.execution import (
    ensure_spot_for_sell as _ensure_spot_for_sell,
)
from crypto_intel_engine.execution import (
    execute_funding as _execute_funding,
)
from crypto_intel_engine.execution import (
    get_recent_crypto_closed_pnls,  # noqa: F401 — 供测试从本模块 import（历史兼容再导出）
)
from crypto_intel_engine.execution import (
    plan_funding as _plan_funding,
)
from crypto_intel_engine.execution import (
    record_crypto_resting as _record_crypto_resting,
)
from crypto_intel_engine.execution import (
    record_crypto_trade as _record_crypto_trade,
)
from crypto_intel_engine.execution import (
    resolve_qty_price as _resolve_qty_price,
)
from crypto_intel_engine.execution import (
    risk_check as _risk_check,
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


def _primary_wallet(breakdown: dict | None) -> str:
    """持仓主钱包（份额最大的那个）→ 前端徽章。空明细按现货兜底（历史/paper 无明细时）。"""
    if not breakdown:
        return "spot"
    return max(breakdown.items(), key=lambda kv: kv[1])[0]


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
                  "current_price": p.current_price, "market_value": round(p.market_value, 2),
                  "available": p.available, "wallet_breakdown": p.wallet_breakdown,
                  "wallet": _primary_wallet(p.wallet_breakdown),
                  # 成本/浮盈亏按成交明细回放重建（交易所不给成本价），带可信度
                  "avg_cost": p.avg_cost or None,
                  "unrealized_pnl": p.unrealized_pnl,
                  "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 2) if p.avg_cost else None,
                  "cost_basis_quality": p.cost_basis_quality,
                  "cost_basis_note": p.cost_basis_note}
                 for p in broker.get_positions()]
    data = {"account": acct, "positions": positions}
    return ToolEnvelope(data=data, widget=crypto_account_widget(data))


@tool(
    name="sync_crypto_fills",
    description="从币安拉取历史成交明细，重建持仓成本价与浮动盈亏。"
                "用户问「我的成本价是多少 / 我这个币赚了多少 / 为什么看不到成本」时先调这个，"
                "再调 get_crypto_account 看结果。首次会拉全部历史，之后是增量。",
    args_model=NoArgs, category="crypto", group="crypto",
)
def sync_crypto_fills() -> ToolEnvelope:
    from acquisition.markets import binance_trade as bt
    if not bt.has_credentials():
        return ToolEnvelope(business_result="negative",
                            message="币安 API key 未配置（.env 的 BINANCE_API_KEY/SECRET），无法拉成交明细")
    from crypto_intel_engine import cost_basis as cb
    r = cb.sync_held_fills()
    if r.get("skipped"):
        return ToolEnvelope(business_result="negative", message="币安凭证不可用，未同步")
    msg = f"已同步 {r['symbols']} 个交易对的成交明细，新增 {r['inserted']} 笔。"
    if r.get("errors"):
        msg += f"⚠️ 这些币没拉全（网络/限速）：{'、'.join(r['errors'])}，成本覆盖度会偏低。"
    msg += ("\n\n注意：链上充值、空投、理财利息、法币买币进来的币**本来就没有成本记录**，"
            "币安也算不出来——这部分会在持仓里标成「成本未知/部分覆盖」，不会拿现价冒充成本。")
    return ToolEnvelope(data=r, message=msg)


# ──────────────────── 下单（需确认 + 风控 + key）────────────────────


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

    # 卖出自动腾挪：币在活期理财/资金钱包时先赎回+划转到现货再卖，否则交易所拒（现货余额不足）
    if action == "SELL":
        spot_ok, spot_reason = _ensure_spot_for_sell(broker, symbol, qty)
        if not spot_ok:
            return ToolEnvelope(business_result="negative",
                                data={"executed": False,
                                      "reason": f"{spot_reason}，未下单"})

    # 幂等键：一次性随机 id。请求超时时 broker 会拿它回查真实状态，而不是闷头判失败
    import secrets
    order = broker.submit_order(symbol, action, qty, price,
                                client_order_id=f"FINCHAT-{secrets.token_hex(6)}")

    # 真实成交量以交易所回执为准，绝不用委托量兜底（否则挂单会被谎报成已成交）。
    # ⛔ 判定顺序：**先看成交量，再看状态**。币安市价单在薄盘/价格带/STP 场景会返回
    # EXPIRED（映射成 REJECTED）**同时带 executedQty > 0**，只看状态会把「已经买到一部分」
    # 判成完全失败 → 不落台账 → 账上凭空多币、连亏风控回放漏笔。
    filled_qty = order.filled_quantity or 0.0
    terminal_bad = order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED,
                                    OrderStatus.FAILED)

    if order.status == OrderStatus.UNKNOWN:
        # 请求已发出但结果不明（超时且回查未果）——绝不能说「失败了」让 Jason 重下
        return ToolEnvelope(
            business_result="negative",
            data={"executed": False, "state_unknown": True, "symbol": symbol,
                  "action": action, "reason": order.error_msg},
            message=(f"⚠️ {symbol} 的{'买入' if action == 'BUY' else '卖出'}请求已发出，"
                     f"但没能拿到回执，**这笔单可能已经成交**。请先去币安核对成交记录再决定是否重下，"
                     f"不要直接重试。详情：{order.error_msg}"),
        )

    if filled_qty <= 0:
        if terminal_bad:
            return ToolEnvelope(business_result="negative",
                                data={"executed": False, "reason": order.error_msg or "执行失败"})
        # 已受理（affirmative）但**未成交**：不发成交事件、不落台账，
        # executed=False/resting=True 把「挂单≠成交」如实传出去。
        _record_crypto_resting(symbol, action, price, order.order_id,
                               order_type="LIMIT" if price else "MARKET")
        msg = (f"限价单已挂出（order={order.order_id}），当前未成交，挂在盘口等待撮合。"
               f"成交后才会计入台账与盈亏。" if price else
               f"市价单已提交（order={order.order_id}）但**零成交**——通常是盘口太薄或该交易对"
               f"暂停交易。没有计入台账，请去币安核对。")
        return ToolEnvelope(
            data={"executed": False, "resting": True, "symbol": symbol, "action": action,
                  "order_id": order.order_id, "status": order.status.value,
                  "price": round(price, 4) if price else None, "quantity": qty},
            message=msg,
        )

    fill_price = order.filled_price or ref
    # terminal_bad 且 filled>0 = 部分成交后余量被撤/过期：成交那部分是真的，必须落账
    partial = terminal_bad or filled_qty < qty
    # 归因（S1）：经 MoneyBill 对话下的单。⚠️ `ai_advice` 说的是**渠道**（AI 在环）
    # 不是「AI 拍的板」—— 确认键是 Jason 按的。`source_ref` 现在留空：`confirm_gate`
    # 在本工具**返回之后**才写 DecisionLog，下单这一刻还没有 decision_id，接它属于 S4。
    _record_crypto_trade(symbol, action, fill_price, filled_qty, order.order_id,
                         commission=order.commission or 0.0,
                         source_kind=TRADE_AI_ADVICE)

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
