"""
交易类工具 —— 仓位换算（只读）+ 模拟盘下单（需二次确认 + 强制风控）。

⚠️ 安全：place_order 标记 requires_confirmation=True，orchestrator 会先中断让用户确认；
即便确认，执行路径仍强制过 RiskManager.check_order，风控不通过一律拒单。LLM 无法绕过。

风控拒单 / 拿不到价格 属于「诚实的否」，用 ToolEnvelope(business_result="negative")
表达，不是 ok=False —— 工具本身正常跑完了，只是结论是拒绝/无法执行。
"""
from typing import TYPE_CHECKING, Any, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

from common.market import A_SHARE
from common.market_time import market_today
from agents.widgets import metric_cards_widget

if TYPE_CHECKING:
    from trading_engine.brokers.paper_broker import PaperBroker


def _resolve_price(symbol: str, price=None) -> float | None:
    """没给价就取最新价（实时优先，回退到最近收盘）。"""
    if price:
        return float(price)
    try:
        from data_engine.engine import DataEngine
        eng = DataEngine()
        quotes = eng.get_realtime_quotes([symbol])
        if quotes:
            p = quotes[0].get("price") or quotes[0].get("close")
            if p:
                return float(p)
        latest = eng.get_latest_date(symbol)
        if latest:
            df = eng.get_daily_data(symbol, "2000-01-01", latest.strftime("%Y-%m-%d"), db_only=True)
            if df is not None and not df.empty:
                return float(df["close"].iloc[-1])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"取最新价失败 {symbol}: {e}")
    return None


def _paper_broker_info() -> tuple["PaperBroker", dict[str, Any]]:
    """本文件的纸面账户。

    ⚠️ 写死 `A_SHARE` 是**如实反映现状**，不是偷懒：这一整个文件都是 A 股口径
    （成交日走 `market_today(A_SHARE)`、金额用 ¥、名称查 `StockInfo`）。
    ⛔ 别在这儿按 symbol 推市场 —— 那会让美股的单落进 A 股的账，还带上印花税。
    要支持别的市场得整文件一起改，不是改这一行。

    🔴 本金没配时会抛 `MarketCapitalNotConfiguredError`（fail-closed），
    调用方必须接住并说人话，别让它变成一条 traceback。
    """
    from trading_engine.brokers.paper_broker import get_paper_broker
    paper = get_paper_broker(A_SHARE)
    acct = paper.get_account_info()
    positions_map = {
        sym: {"market_value": pos.market_value, "avg_cost": pos.avg_cost,
              "current_price": pos.current_price}
        for sym, pos in paper.positions.items()
    }
    return paper, {
        "cash": acct["cash"], "market_value": acct["market_value"],
        "total_value": acct["total_value"], "unrealized_pnl": acct["unrealized_pnl"],
        "positions": positions_map, "recent_closed_pnls": [], "last_loss_date": None,
    }


def _risk_check(
    symbol: str, action: str, quantity: int, price: float, broker_info: dict,
) -> tuple[bool, list[str], list[str]]:
    from trading_engine.risk.manager import RiskManager
    from trading_engine.risk.adapter import get_effective_risk_config
    rm = RiskManager(get_effective_risk_config())
    passed, results = rm.check_order(symbol=symbol, action=action,
                                     quantity=quantity, price=price, broker_info=broker_info)
    msgs = [r.message for r in results]
    failed = [r.message for r in results if not r.passed]
    return passed, msgs, failed


def _norm(args: dict) -> tuple[str, str, int, Optional[float]]:
    symbol = (args.get("symbol") or "").strip()
    side = (args.get("side") or "").strip().lower()
    action = "BUY" if side in ("buy", "买", "买入") else "SELL"
    qty = int(args.get("quantity") or 0)
    price = _resolve_price(symbol, args.get("price"))
    return symbol, action, qty, price


# ──────────────────── 仓位换算（只读） ────────────────────

class SizePositionArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码")
    target_pct: float = Field(..., gt=0, le=100, description="目标仓位占总资金百分比，如 5 表示 5%")
    price: Optional[float] = Field(None, gt=0, description="可选，价格；不填取最新价")


@tool(
    name="size_position",
    description=(
        "把「目标仓位百分比」换算成可执行的股数/金额，受现金、单股集中度上限、风控三重约束。"
        "下单前算「该买多少」的唯一入口——即便 cockpit/recommend 给过建议股数，正式下单前也以此为准。"
    ),
    args_model=SizePositionArgs,
    category="trading",
    group="core",
)
def size_position_tool(symbol: str, target_pct: float, price=None) -> ToolEnvelope:
    from trading_engine.position_sizing import size_position
    from trading_engine.risk.adapter import get_total_capital, get_max_position_pct, build_broker_info
    p = _resolve_price(symbol, price)
    if not p:
        return ToolEnvelope(business_result="negative", message=f"拿不到 {symbol} 的价格，无法换算")
    total = get_total_capital()
    r = size_position(symbol, p, float(target_pct) / 100.0,
                      broker_info=build_broker_info(total), total_capital=total,
                      max_position_pct=get_max_position_pct())
    return ToolEnvelope(data=r)


# ──────────────────── 下单（需确认 + 风控） ────────────────────

def preview_order(args: dict) -> dict:
    """确认前预览：订单详情 + 风控预检（不执行）。"""
    symbol, action, qty, price = _norm(args)
    if not symbol or qty <= 0:
        return {"error": "缺少股票代码或数量"}
    if not price:
        return {"error": f"拿不到 {symbol} 的价格"}
    from trading_engine.brokers.paper_broker import MarketCapitalNotConfiguredError
    try:
        _, broker_info = _paper_broker_info()
    except MarketCapitalNotConfiguredError as e:
        return {"error": str(e)}
    passed, msgs, failed = _risk_check(symbol, action, qty, price, broker_info)
    return {
        "symbol": symbol, "action": action, "quantity": qty, "price": round(price, 3),
        "est_amount": round(price * qty, 2), "cash": round(broker_info["cash"], 2),
        "broker": "paper（模拟盘）",
        "risk_passed": passed, "risk_checks": msgs, "risk_failed": failed,
    }


class PlaceOrderArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    side: Literal["buy", "sell"] = Field(..., description="买入或卖出")
    quantity: int = Field(..., gt=0, description="股数（A股需 100 的整数倍）")
    price: Optional[float] = Field(None, gt=0, description="可选，限价；不填按最新价")


@tool(
    name="place_order",
    description="【下单·模拟盘】买入或卖出股票。会先让 Jason 二次确认，并强制通过风控才执行。"
                "用户明确要「买/卖/下单 N 股某股票」时调用。",
    args_model=PlaceOrderArgs,
    category="trading",
    group="core",
    requires_confirmation=True,
    preview_fn=preview_order,
)
def place_order(symbol: str, side: str, quantity: int, price=None) -> ToolEnvelope:
    symbol_n, action, qty, p = _norm({"symbol": symbol, "side": side, "quantity": quantity, "price": price})
    if not symbol_n or qty <= 0:
        return ToolEnvelope(business_result="negative", message="下单失败：缺少股票代码或数量")
    if not p:
        return ToolEnvelope(business_result="negative", message=f"下单失败：拿不到 {symbol_n} 的价格")

    # 强制风控（即便已确认也不可绕过）
    from trading_engine.brokers.paper_broker import MarketCapitalNotConfiguredError
    try:
        paper, broker_info = _paper_broker_info()
    except MarketCapitalNotConfiguredError as e:
        return ToolEnvelope(business_result="negative", message=f"下单失败：{e}")
    passed, msgs, failed = _risk_check(symbol_n, action, qty, p, broker_info)
    if not passed:
        return ToolEnvelope(business_result="negative",
                             data={"executed": False, "reason": "风控未通过", "failed_rules": failed})

    # 执行模拟盘下单
    from trading_engine.brokers.base import OrderStatus
    paper.update_market_price(symbol_n, p)
    order = paper.submit_order(symbol_n, action, qty, p)
    if order.status not in (OrderStatus.FILLED, OrderStatus.SUBMITTED):
        return ToolEnvelope(business_result="negative",
                             data={"executed": False, "reason": order.error_msg or "执行失败"})

    fill_price = order.filled_price or p
    fill_qty = order.filled_quantity or qty
    commission = order.commission or 0.0

    # 写入 ManualTrade（与 Portfolio 兼容）
    try:
        from data_engine.storage.database import get_session
        from data_engine.storage.models import ManualTrade, StockInfo
        session = get_session()
        try:
            info = session.query(StockInfo).filter(StockInfo.symbol == symbol_n).first()
            name = info.name if info else symbol_n
            trade = ManualTrade(
                symbol=symbol_n, name=name, side=action, price=fill_price, quantity=fill_qty,
                amount=fill_price * fill_qty, commission=commission, trade_date=market_today(A_SHARE),
                note="[MoneyBill] 对话下单", source_type="moneybill",
            )
            session.add(trade)
            session.flush()
            if action == "SELL":
                try:
                    from portfolio.closed_trade_service import ClosedTradeService
                    ClosedTradeService().generate_closed_trade_for_sell(trade.id)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"SELL 生成已平仓记录失败: {e}")
            session.commit()
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001
        logger.error(f"写入 ManualTrade 失败: {e}")

    # 发业务事件：MoneyBill 对话下单成交 → 汇入事件总线
    try:
        from business_events import publish_event, ORDER_FILLED
        publish_event(
            ORDER_FILLED, source="moneybill", symbol=symbol_n,
            title=f"{'买入' if action == 'BUY' else '卖出'} {symbol_n} {fill_qty}股 @ ¥{fill_price:.2f}（模拟盘）",
            action=action, price=round(fill_price, 3), quantity=fill_qty,
            amount=round(fill_price * fill_qty, 2),
        )
    except Exception:  # noqa: BLE001
        pass

    widget = metric_cards_widget([
        {"label": "方向", "value": "买入" if action == "BUY" else "卖出", "type": "neutral"},
        {"label": "成交价", "value": f"¥{fill_price:.2f}", "type": "neutral"},
        {"label": "数量", "value": f"{fill_qty} 股", "type": "neutral"},
        {"label": "金额", "value": f"¥{fill_price * fill_qty:,.0f}", "type": "neutral"},
    ], title=f"✅ 模拟盘已成交 · {symbol_n}")
    return ToolEnvelope(
        data={"executed": True, "symbol": symbol_n, "action": action,
              "price": round(fill_price, 3), "quantity": fill_qty,
              "amount": round(fill_price * fill_qty, 2)},
        widget=widget,
    )
