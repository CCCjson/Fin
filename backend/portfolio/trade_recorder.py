"""
手动交易录入服务 — ManualTrade 落库的唯一入口（除模拟盘 place_order 外）。

原为 api/routes/portfolio.py 的 create_trade 逻辑下沉而来（该 route 已在 MoneyBill
改造中退役）：卖出持仓校验 → 自动补金额/名称 → 落库 → SELL 自动生成已平仓记录 →
风控警告（不阻断）。现由 agents/tools/portfolio_tools.py 的 record_manual_trade 工具调用。
"""
import re
from datetime import date
from typing import Optional

from loguru import logger

# A股代码格式: 6位数字 + .SH 或 .SZ
_A_SHARE_PATTERN = re.compile(r'^\d{6}\.(SH|SZ)$')


def record_trade(
    *,
    symbol: str,
    side: str,
    price: float,
    quantity: int,
    trade_date: str,
    name: Optional[str] = None,
    amount: Optional[float] = None,
    commission: float = 0.0,
    note: Optional[str] = None,
    source_type: Optional[str] = None,
    report_id: Optional[str] = None,
    ai_recommended_price: Optional[float] = None,
    ai_stop_loss: Optional[float] = None,
    ai_take_profit: Optional[float] = None,
    ai_composite_score: Optional[float] = None,
    ai_strategy: Optional[str] = None,
) -> dict:
    """录入一笔手动交易。校验失败抛 ValueError；返回 trade dict + risk_warnings。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import ManualTrade, StockInfo
    from portfolio.calculator import PortfolioCalculator
    from portfolio.closed_trade_service import ClosedTradeService
    from trading_engine.risk.manager import RiskManager
    from trading_engine.risk.adapter import build_broker_info, get_effective_risk_config

    symbol = (symbol or "").strip().upper()
    if not _A_SHARE_PATTERN.match(symbol):
        raise ValueError(f"股票代码格式错误: {symbol}，应为6位数字+.SH或.SZ，如 600519.SH")
    side = (side or "").strip().upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side 必须是 BUY 或 SELL，收到: {side}")
    if price <= 0 or quantity <= 0:
        raise ValueError("price 和 quantity 必须为正数")

    session = get_session()
    try:
        # 卖出时校验持仓是否足够
        if side == "SELL":
            current_positions = {
                p["symbol"]: p["quantity"]
                for p in PortfolioCalculator().get_current_positions()
            }
            held_qty = current_positions.get(symbol, 0)
            if quantity > held_qty:
                raise ValueError(f"持仓不足: {symbol} 当前持有 {held_qty} 股，卖出 {quantity} 股")

        # 自动计算金额 / 查找名称
        amount = amount if amount is not None else price * quantity
        if not name:
            stock = session.query(StockInfo).filter(StockInfo.symbol == symbol).first()
            if stock:
                name = stock.name

        trade = ManualTrade(
            symbol=symbol,
            name=name,
            side=side,
            price=price,
            quantity=quantity,
            amount=amount,
            commission=commission,
            trade_date=date.fromisoformat(trade_date),
            note=note,
            report_id=report_id,
            ai_recommended_price=ai_recommended_price,
            ai_stop_loss=ai_stop_loss,
            ai_take_profit=ai_take_profit,
            ai_composite_score=ai_composite_score,
            ai_strategy=ai_strategy,
        )
        if source_type:
            trade.source_type = source_type

        session.add(trade)
        session.commit()
        session.refresh(trade)
        logger.info(f"录入交易: {trade.side} {trade.symbol} x{trade.quantity} @{trade.price}")

        # --- SELL 交易自动生成已平仓记录 ---
        if trade.side == "SELL":
            try:
                ClosedTradeService().generate_closed_trade_for_sell(trade.id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"自动生成已平仓记录失败（不影响录入）: {e}")

        # --- 风控检查（不阻断录入，仅产生警告） ---
        risk_warnings = []
        try:
            rm = RiskManager(config=get_effective_risk_config())
            _, results = rm.check_order(
                symbol=trade.symbol,
                action=trade.side,
                quantity=trade.quantity,
                price=trade.price,
                broker_info=build_broker_info(),
            )
            risk_warnings = [
                {"rule": r.rule_name, "message": r.message, "severity": r.severity}
                for r in results if not r.passed
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"风控检查异常（不影响录入）: {e}")

        return {
            "id": trade.id,
            "symbol": trade.symbol,
            "name": trade.name,
            "side": trade.side,
            "price": trade.price,
            "quantity": trade.quantity,
            "amount": trade.amount,
            "commission": trade.commission,
            "trade_date": str(trade.trade_date),
            "note": trade.note,
            "risk_warnings": risk_warnings,
        }
    except ValueError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
