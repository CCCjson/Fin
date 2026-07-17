"""
组合类工具 —— 当前持仓、组合绩效、组合风险、对账情况（只读）。
"""
from typing import Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

_calc = None


def _get_calc():
    global _calc
    if _calc is None:
        from portfolio.calculator import PortfolioCalculator
        _calc = PortfolioCalculator()
    return _calc


def _overlay_realtime_prices(held: list) -> str:
    """盘中用实时价覆盖持仓的市值/浮盈亏，并补当日盈亏；返回 price_as_of。

    不动 PortfolioCalculator 本体（EOD 口径别处还在用），只在工具层叠加。

    返回值三态（2026-07-17，P0-2 拆开）：
      `realtime`         盘中且实时价拿到了
      `eod`              **非盘中** —— EOD 就是正确口径，这是正常态不是降级
      `eod_fetch_failed` **盘中但实时价没拿到** —— 你正盯着一个昨收价当现价看

    此前三者都返回 `eod`：`except Exception: return "eod"` 把「市场关着呢，昨收
    天经地义」和「盘中实时抓挂了，你看的是昨天的价」说成同一件事。前者无需任何
    动作，后者是**必须让人知道**的降级 —— 这正是 P0-2 要消灭的那类塌缩。
    """
    from agents.tools.recommend_tools import _session_phase
    if _session_phase() != "intraday" or not held:
        return "eod"
    try:
        from acquisition.markets.realtime import fetch_quotes_by_symbols
        quotes = {q["symbol"]: q for q in
                  fetch_quotes_by_symbols([p["symbol"] for p in held])}
    except Exception as e:  # noqa: BLE001 — 取价失败不该掀翻整个持仓查询
        logger.warning(f"持仓实时价叠加失败，回退 EOD（盘中，已如实标记）: {e}")
        return "eod_fetch_failed"
    if not quotes:
        logger.warning("持仓实时价一只都没拿到，回退 EOD（盘中，已如实标记）")
        return "eod_fetch_failed"
    for p in held:
        q = quotes.get(p["symbol"])
        price = (q or {}).get("price")
        if not price:
            continue
        qty = p.get("quantity") or 0
        cost = p.get("total_cost") or 0
        p["current_price"] = price
        p["market_value"] = round(price * qty, 2)
        p["unrealized_pnl"] = round(price * qty - cost, 2)
        p["unrealized_pnl_pct"] = (round((price * qty - cost) / cost * 100, 2)
                                   if cost > 0 else None)
        p["day_change_pct"] = q.get("change_percent")
        if q.get("prev_close"):
            p["day_pnl"] = round((price - q["prev_close"]) * qty, 2)
    return "realtime"


class GetPositionsArgs(BaseModel):
    pass


@tool(
    name="get_positions",
    description=(
        "获取用户当前所有持仓（代码、名称、数量、成本均价、浮动/已实现盈亏）。"
        "盘中自动用实时价计算市值与当日盈亏。"
        "回答「我现在持有什么/仓位多少/今天赚亏多少」时用。"
        "**price_as_of 三态**：realtime=盘中实时价；eod=非盘中，收盘价是正确口径；"
        "eod_fetch_failed=盘中但实时价没取到，数字是昨收——**这种情况必须告诉 Jason，"
        "别把昨收当现价讲**。"
    ),
    args_model=GetPositionsArgs,
    category="portfolio",
    group="core",
)
def get_positions() -> ToolEnvelope:
    from agents.widgets import position_table_widget
    positions = _get_calc().get_current_positions()
    held = [p for p in positions if p.get("quantity", 0) > 0]
    price_as_of = _overlay_realtime_prices(held)
    if not held:
        return ToolEnvelope(business_result="negative", message="当前无持仓。",
                             data={"count": 0, "price_as_of": price_as_of, "positions": []})
    widget = position_table_widget(held)
    return ToolEnvelope(data={"count": len(held), "price_as_of": price_as_of,
                              "positions": held}, widget=widget)


class GetPositionGuardStatusArgs(BaseModel):
    pass


@tool(
    name="get_position_guard_status",
    description=(
        "持仓盘中守护状态：每只持仓的实时价、当日盈亏、距止损/止盈的距离和风险级别"
        "（danger=已破止损/warning=接近止损/take_profit=触发止盈/safe）。"
        "后台守护会盘中自动监控并弹提醒，本工具用于主动查看。"
        "回答「我的持仓现在安全吗/有没有票快到止损了」时用。"
    ),
    args_model=GetPositionGuardStatusArgs,
    category="portfolio",
    group="portfolio_risk",
)
def get_position_guard_status() -> ToolEnvelope:
    from automation.position_guardian import build_guard_status
    from agents.widgets import metric_cards_widget

    status = build_guard_status()
    rows = status.get("positions") or []
    if not rows:
        return ToolEnvelope(business_result="negative", message="当前无持仓，守护无事可做。")

    risky = [r for r in rows if r["level"] in ("danger", "warning", "take_profit")]
    cards = [
        {"label": f"{r['name']}", "value": f"{r['unrealized_pnl_pct']}%"
         if r["unrealized_pnl_pct"] is not None else "—",
         "type": "risk", "positive": r["level"] in ("safe", "near_tp", "take_profit")}
        for r in rows[:6]
    ]
    return ToolEnvelope(
        data={
            "risk_count": len(risky),
            "stop_loss_line_pct": status.get("stop_loss_pct"),
            "take_profit_line_pct": status.get("take_profit_pct"),
            "guard_running": status.get("guard_running"),
            "positions": rows,
        },
        widget=metric_cards_widget(cards, title="🛡️ 持仓守护"),
    )


class GetPerformanceArgs(BaseModel):
    pass


@tool(
    name="get_performance",
    description="获取组合整体绩效统计（总收益、胜率、盈亏比等）。回答「我赚了多少/表现如何」时用。",
    args_model=GetPerformanceArgs,
    category="portfolio",
    group="core",
)
def get_performance() -> ToolEnvelope:
    from agents.widgets import performance_metric_cards
    stats = _get_calc().get_performance_stats()
    return ToolEnvelope(data=stats, widget=performance_metric_cards(stats))


class GetPortfolioRiskArgs(BaseModel):
    pass


@tool(
    name="get_portfolio_risk",
    description=(
        "组合层面风险体检：Beta(vs沪深300)、行业集中度、持仓间相关性、组合最大回撤、"
        "流动性风险，给出综合风险评级（低/中/高）。回答「我的组合风险大吗/持仓够分散吗」。"
        "涉及联网取行业与指数数据，稍慢。"
    ),
    args_model=GetPortfolioRiskArgs,
    category="portfolio",
    group="portfolio_risk",
)
def get_portfolio_risk() -> ToolEnvelope:
    from portfolio.risk_analyzer import PortfolioRiskAnalyzer
    from agents.widgets import metric_cards_widget

    positions = [p for p in _get_calc().get_current_positions()
                 if (p.get("quantity") or 0) > 0]
    if not positions:
        return ToolEnvelope(business_result="negative", message="当前无持仓，没有组合风险可分析。")

    r = PortfolioRiskAnalyzer().analyze(positions)
    if r.get("error") and r.get("risk_level") == "N/A":
        return ToolEnvelope(business_result="negative", message=f"组合风险分析失败：{r['error']}")

    beta = (r.get("beta") or {}).get("value")
    conc = r.get("industry_concentration") or {}
    corr = (r.get("correlation") or {}).get("value")
    dd = (r.get("max_drawdown") or {}).get("value")
    summary = {
        "risk_level": r.get("risk_level"),
        "position_count": r.get("position_count"),
        "total_market_value": r.get("total_market_value"),
        "beta": r.get("beta"),
        "industry_concentration": {
            "top3": conc.get("top3"), "top3_pct": conc.get("top3_pct"),
            "interpretation": conc.get("interpretation"),
        },
        "correlation": r.get("correlation"),
        "max_drawdown": r.get("max_drawdown"),
        "liquidity": {
            "high_risk_count": (r.get("liquidity") or {}).get("high_risk_count"),
            "interpretation": (r.get("liquidity") or {}).get("interpretation"),
        },
    }
    cards = [
        {"label": "风险评级", "value": r.get("risk_level") or "—", "type": "risk",
         "positive": r.get("risk_level") == "低"},
        {"label": "Beta", "value": str(beta) if beta is not None else "—", "type": "neutral"},
        {"label": "行业Top3", "value": f"{conc.get('top3_pct')}%"
         if conc.get("top3_pct") is not None else "—", "type": "neutral"},
        {"label": "平均相关性", "value": str(corr) if corr is not None else "—", "type": "neutral"},
        {"label": "最大回撤", "value": f"{dd}%" if dd is not None else "—", "type": "risk",
         "positive": (dd or 0) > -8},
    ]
    return ToolEnvelope(data=summary, widget=metric_cards_widget(cards, title="🛡️ 组合风险体检"))


class GetReconciliationStatusArgs(BaseModel):
    limit: int = Field(5, ge=1, le=20, description="最多返回条数，默认 5")


@tool(
    name="get_reconciliation_status",
    description=(
        "查持仓对账历史（只读）：最近几次「系统持仓 vs 券商真实持仓」的对账结果与调整明细。"
        "回答「上次对账什么情况/系统账实相符吗」。录入交易和应用对账调整请在持仓页面操作。"
    ),
    args_model=GetReconciliationStatusArgs,
    category="portfolio",
    group="portfolio_risk",
)
def get_reconciliation_status(limit: int = 5) -> ToolEnvelope:
    from portfolio.reconciliation_service import ReconciliationService
    limit = max(1, min(int(limit or 5), 20))
    records = ReconciliationService().history(limit=limit)
    if not records:
        return ToolEnvelope(business_result="negative", message="还没有做过持仓对账。可在持仓页面粘贴券商持仓发起对账。")
    out = [
        {
            "reconcile_date": r.get("reconcile_date"),
            "status": r.get("status"),
            "summary": r.get("summary"),
            "adjustments_count": len(r.get("adjustments") or []),
            "adjustments": r.get("adjustments"),
            "note": r.get("note"),
        }
        for r in records
    ]
    return ToolEnvelope(data={"count": len(out), "records": out, "latest": out[0] if out else None})


class GetTradeHistoryArgs(BaseModel):
    symbol: Optional[str] = Field(None, description="股票代码筛选，可选")
    side: Optional[Literal["BUY", "SELL"]] = Field(None, description="方向筛选，可选")
    start_date: Optional[str] = Field(None, description="开始日期 YYYY-MM-DD，可选")
    end_date: Optional[str] = Field(None, description="结束日期 YYYY-MM-DD，可选")
    limit: int = Field(20, ge=1, le=100, description="返回条数，默认 20，最多 100")


@tool(
    name="get_trade_history",
    description=(
        "查询历史交易记录（手动录入+对话下单的全部成交流水），支持按股票/方向/日期筛选。"
        "用户问「我什么时候买的XX / 最近交易了什么 / 交易流水」时调用。"
    ),
    args_model=GetTradeHistoryArgs,
    category="portfolio",
    group="portfolio_risk",
)
def get_trade_history(symbol: str = "", side: str = "", start_date: str = "",
                      end_date: str = "", limit: int = 20) -> ToolEnvelope:
    from datetime import date as _date
    from data_engine.storage.database import get_session
    from data_engine.storage.models import ManualTrade

    limit = max(1, min(int(limit or 20), 100))
    session = get_session()
    try:
        query = session.query(ManualTrade)
        if symbol:
            query = query.filter(ManualTrade.symbol == symbol.strip().upper())
        if side:
            query = query.filter(ManualTrade.side == side.strip().upper())
        if start_date:
            query = query.filter(ManualTrade.trade_date >= _date.fromisoformat(start_date))
        if end_date:
            query = query.filter(ManualTrade.trade_date <= _date.fromisoformat(end_date))
        total = query.count()
        rows = (query.order_by(ManualTrade.trade_date.desc(), ManualTrade.id.desc())
                .limit(limit).all())
        trades = [
            {
                "date": str(t.trade_date), "symbol": t.symbol, "name": t.name,
                "side": t.side, "price": t.price, "quantity": t.quantity,
                "amount": t.amount, "commission": t.commission,
                "note": t.note, "source": getattr(t, "source_type", None),
            }
            for t in rows
        ]
        if not trades:
            return ToolEnvelope(business_result="negative", message="没有符合条件的交易记录。",
                                 data={"total": total, "shown": 0, "trades": []})
        return ToolEnvelope(data={"total": total, "shown": len(trades), "trades": trades})
    finally:
        session.close()


def _preview_record_trade(args: dict) -> dict:
    """确认前预览：这笔补录的方向/标的/价格/数量/金额。"""
    symbol = (args.get("symbol") or "").strip().upper()
    side = (args.get("side") or "").strip().upper()
    price = float(args.get("price") or 0)
    qty = int(args.get("quantity") or 0)
    if not symbol or side not in ("BUY", "SELL") or price <= 0 or qty <= 0:
        return {"error": "缺少或非法的 symbol/side/price/quantity"}
    return {
        "action": "补录真实成交（不走模拟盘，只记账）",
        "symbol": symbol, "side": side, "price": price, "quantity": qty,
        "est_amount": round(price * qty, 2),
        "trade_date": args.get("trade_date") or "今天",
        "note": "录入后计入持仓与绩效；SELL 会自动生成已平仓记录。",
    }


class RecordManualTradeArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    side: Literal["BUY", "SELL"] = Field(..., description="买入或卖出")
    price: float = Field(..., gt=0, description="成交价")
    quantity: int = Field(..., gt=0, description="成交数量（股）")
    trade_date: str = Field("", description="成交日期 YYYY-MM-DD，默认今天")
    commission: float = Field(0.0, ge=0, description="手续费，可选")
    note: str = Field("", description="备注，可选")


@tool(
    name="record_manual_trade",
    description=(
        "【补录真实成交】把 Jason 在券商 App 实际成交的一笔交易补录进系统（只记账，不走模拟盘下单）。"
        "用户说「我刚在券商买/卖了XX，帮我记一下」「补录一笔成交」时调用。会先让 Jason 二次确认。"
    ),
    args_model=RecordManualTradeArgs,
    category="portfolio",
    group="portfolio_risk",
    requires_confirmation=True,
    preview_fn=_preview_record_trade,
)
def record_manual_trade(symbol: str, side: str, price: float, quantity: int,
                        trade_date: str = "", commission: float = 0.0,
                        note: str = "") -> ToolEnvelope:
    from datetime import date as _date
    from portfolio.trade_recorder import record_trade

    try:
        result = record_trade(
            symbol=symbol, side=side, price=float(price), quantity=int(quantity),
            trade_date=trade_date or _date.today().isoformat(),
            commission=float(commission or 0.0),
            note=note or "[MoneyBill] 对话补录真实成交",
            source_type="moneybill_manual",
        )
    except ValueError as e:
        return ToolEnvelope(business_result="negative", message=f"补录失败：{e}")

    warns = result.get("risk_warnings") or []
    return ToolEnvelope(data={
        "recorded": f"{result['side']} {result['name'] or result['symbol']} "
                    f"{result['quantity']}股 @ ¥{result['price']}（{result['trade_date']}）",
        "amount": result["amount"],
        "risk_warnings": warns or "无",
    })
