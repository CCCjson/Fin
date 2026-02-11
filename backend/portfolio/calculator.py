"""
持仓计算器 — 基于手动交易记录计算当前持仓和绩效统计

使用成本均价法（A股券商标准算法）:
- BUY:  avg_cost = (旧总成本 + 新金额 + 佣金) / 总持股
- SELL: realized_pnl += (卖价 * 数量 - 佣金) - (均价 * 数量)
"""
from typing import List, Dict, Any
from collections import defaultdict
from datetime import date

from sqlalchemy import func as sql_func
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import ManualTrade, DailyQuote, StockInfo


class PortfolioCalculator:
    """持仓与绩效计算器"""

    def get_current_positions(self, as_of_date: date | None = None) -> List[Dict[str, Any]]:
        """
        计算持仓（成本均价法）

        Args:
            as_of_date: 截止日期，只回放该日期及之前的交易。None 表示全部交易。

        Returns:
            每只持仓股票的详细信息列表
        """
        session = get_session()
        try:
            query = session.query(ManualTrade)
            if as_of_date is not None:
                query = query.filter(ManualTrade.trade_date <= as_of_date)
            trades = (
                query
                .order_by(ManualTrade.trade_date.asc(), ManualTrade.id.asc())
                .all()
            )

            # 按 symbol 分组回放交易
            positions: Dict[str, Dict[str, Any]] = {}

            for t in trades:
                sym = t.symbol
                if sym not in positions:
                    positions[sym] = {
                        "symbol": sym,
                        "name": t.name or sym,
                        "quantity": 0,
                        "total_cost": 0.0,
                        "avg_cost": 0.0,
                        "realized_pnl": 0.0,
                    }

                pos = positions[sym]

                if t.side == "BUY":
                    new_cost = t.amount + (t.commission or 0)
                    pos["total_cost"] += new_cost
                    pos["quantity"] += t.quantity
                    if pos["quantity"] > 0:
                        pos["avg_cost"] = pos["total_cost"] / pos["quantity"]
                elif t.side == "SELL":
                    sell_revenue = t.amount - (t.commission or 0)
                    cost_basis = pos["avg_cost"] * t.quantity
                    pos["realized_pnl"] += sell_revenue - cost_basis
                    pos["quantity"] -= t.quantity
                    pos["total_cost"] = pos["avg_cost"] * pos["quantity"]

                # 更新名称（取最新的）
                if t.name:
                    pos["name"] = t.name

            # 只返回仍有持仓的
            result = []
            for sym, pos in positions.items():
                if pos["quantity"] <= 0:
                    continue

                current_price = self._get_latest_price(session, sym)

                market_value = current_price * pos["quantity"] if current_price else None
                unrealized_pnl = (market_value - pos["total_cost"]) if market_value is not None else None
                unrealized_pnl_pct = (
                    (unrealized_pnl / pos["total_cost"] * 100) if unrealized_pnl is not None and pos["total_cost"] > 0 else None
                )

                result.append({
                    "symbol": pos["symbol"],
                    "name": pos["name"],
                    "quantity": pos["quantity"],
                    "avg_cost": round(pos["avg_cost"], 4),
                    "current_price": current_price,
                    "market_value": round(market_value, 2) if market_value is not None else None,
                    "total_cost": round(pos["total_cost"], 2),
                    "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
                    "unrealized_pnl_pct": round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else None,
                    "realized_pnl": round(pos["realized_pnl"], 2),
                })

            return result

        except Exception as e:
            logger.error(f"计算持仓失败: {e}")
            raise
        finally:
            session.close()

    def get_performance_stats(self) -> Dict[str, Any]:
        """
        计算整体绩效统计

        Returns:
            总投入、当前市值、总盈亏、胜率等
        """
        session = get_session()
        try:
            trades = (
                session.query(ManualTrade)
                .order_by(ManualTrade.trade_date.asc(), ManualTrade.id.asc())
                .all()
            )

            if not trades:
                return self._empty_stats()

            # 回放全部交易，计算已实现盈亏
            positions: Dict[str, Dict[str, Any]] = {}
            closed_trades: List[Dict[str, Any]] = []  # 每笔卖出的盈亏
            total_commission = 0.0
            total_buy_amount = 0.0
            total_sell_amount = 0.0

            for t in trades:
                total_commission += t.commission or 0
                sym = t.symbol

                if sym not in positions:
                    positions[sym] = {
                        "quantity": 0,
                        "total_cost": 0.0,
                        "avg_cost": 0.0,
                    }

                pos = positions[sym]

                if t.side == "BUY":
                    total_buy_amount += t.amount
                    new_cost = t.amount + (t.commission or 0)
                    pos["total_cost"] += new_cost
                    pos["quantity"] += t.quantity
                    if pos["quantity"] > 0:
                        pos["avg_cost"] = pos["total_cost"] / pos["quantity"]

                elif t.side == "SELL":
                    total_sell_amount += t.amount
                    sell_revenue = t.amount - (t.commission or 0)
                    cost_basis = pos["avg_cost"] * t.quantity
                    pnl = sell_revenue - cost_basis
                    closed_trades.append({"pnl": pnl, "symbol": sym})
                    pos["quantity"] -= t.quantity
                    pos["total_cost"] = pos["avg_cost"] * pos["quantity"]

            # 已实现盈亏汇总
            realized_pnl = sum(ct["pnl"] for ct in closed_trades)

            # 当前持仓市值
            current_value = 0.0
            total_cost_holding = 0.0
            for sym, pos in positions.items():
                if pos["quantity"] <= 0:
                    continue
                total_cost_holding += pos["total_cost"]
                price = self._get_latest_price(session, sym)
                if price:
                    current_value += price * pos["quantity"]
                else:
                    current_value += pos["total_cost"]  # 无报价用成本代替

            # 未实现盈亏
            unrealized_pnl = current_value - total_cost_holding

            # 总盈亏
            total_pnl = realized_pnl + unrealized_pnl
            total_invested = total_buy_amount
            total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0

            # 胜率统计（基于已平仓交易）
            win_trades = [ct for ct in closed_trades if ct["pnl"] > 0]
            loss_trades = [ct for ct in closed_trades if ct["pnl"] < 0]
            win_count = len(win_trades)
            loss_count = len(loss_trades)
            total_closed = win_count + loss_count
            win_rate = (win_count / total_closed * 100) if total_closed > 0 else 0

            avg_win = (sum(ct["pnl"] for ct in win_trades) / win_count) if win_count > 0 else 0
            avg_loss = (sum(ct["pnl"] for ct in loss_trades) / loss_count) if loss_count > 0 else 0
            profit_loss_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0

            return {
                "total_invested": round(total_invested, 2),
                "current_value": round(current_value, 2),
                "total_cost_holding": round(total_cost_holding, 2),
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 2),
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": round(win_rate, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_loss_ratio": round(profit_loss_ratio, 2),
                "total_commission": round(total_commission, 2),
                "total_trades": len(trades),
                "total_closed_trades": total_closed,
            }

        except Exception as e:
            logger.error(f"计算绩效统计失败: {e}")
            raise
        finally:
            session.close()

    def _get_latest_price(self, session, symbol: str) -> float | None:
        """获取最新收盘价"""
        quote = (
            session.query(DailyQuote)
            .filter(DailyQuote.symbol == symbol)
            .order_by(DailyQuote.date.desc())
            .first()
        )
        return quote.close if quote else None

    def _empty_stats(self) -> Dict[str, Any]:
        return {
            "total_invested": 0,
            "current_value": 0,
            "total_cost_holding": 0,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_loss_ratio": 0,
            "total_commission": 0,
            "total_trades": 0,
            "total_closed_trades": 0,
        }
