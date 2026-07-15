"""
PendingOrderManager — 自动化交易核心

负责：信号 → 待确认订单 → 用户确认 → 执行 → 写入 ManualTrade
"""
import json
import uuid
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Any

from loguru import logger
from sqlalchemy import and_, or_

from data_engine.storage.database import get_session
from data_engine.storage.models import PendingOrder, ManualTrade, StockInfo
from portfolio.closed_trade_service import ClosedTradeService
from trading_engine.config import RISK_CONFIG
from trading_engine.risk.manager import RiskManager
from trading_engine.risk.adapter import build_broker_info, get_total_capital, get_effective_risk_config
from trading_engine.brokers.paper_broker import PaperBroker
from trading_engine.brokers.base import OrderStatus


# 全局 PaperBroker 实例（进程内共享）
_paper_broker: Optional[PaperBroker] = None


def get_paper_broker() -> PaperBroker:
    """获取全局 PaperBroker 实例"""
    global _paper_broker
    if _paper_broker is None:
        _paper_broker = PaperBroker(initial_cash=get_total_capital())
    return _paper_broker


class PendingOrderManager:
    """待确认订单管理器"""

    # 注意：不在 __init__ 里构造 risk_manager。
    # 一是该实例是 import 期创建的进程单例（automation.py / scheduler.py），
    #   在 __init__ 里查 UserSettings 会在建表前触发查询；
    # 二是把配置抓死会让 UI 改集中度后自动单/定时单不生效。
    # 故风控配置在每次下单校验时实时读取（与 portfolio.py / automation.py 手动单一致）。

    def create_from_signal(
        self,
        signal: Dict[str, Any],
        scan_source: str = "daily",
        broker_type: str = "paper",
        expire_minutes: int = 30,
        position_size_pct: float = 0.10,
    ) -> Optional[Dict]:
        """
        从信号创建待确认订单

        Args:
            signal: 信号字典，需包含 symbol, signal_type, strength, strategy 等字段
            scan_source: 来源 (daily/intraday/position_check)
            broker_type: 券商类型 (paper/easytrader)
            expire_minutes: 过期时间（分钟）
            position_size_pct: 仓位占比

        Returns:
            创建的 PendingOrder 字典，或 None（查重/风控拦截时）
        """
        session = get_session()
        try:
            symbol = signal.get("symbol", "")
            signal_type = signal.get("signal_type", signal.get("type", "BUY")).upper()
            strength = signal.get("strength", 0.5)
            strategy = signal.get("strategy", "")
            price = signal.get("price", signal.get("entry_price", 0))

            if not symbol or price <= 0:
                logger.warning(f"信号数据不完整，跳过: {signal}")
                return None

            # SELL 信号：检查是否真的持有该股票
            if signal_type == "SELL":
                broker = get_paper_broker()
                position = broker.get_position(symbol)
                if not position or position.quantity <= 0:
                    logger.info(f"跳过 SELL 信号（无持仓）: {symbol}")
                    return None

            # 查重：同 symbol + signal_type 30 分钟内不重复创建
            cutoff = datetime.now() - timedelta(minutes=30)
            existing = session.query(PendingOrder).filter(
                and_(
                    PendingOrder.symbol == symbol,
                    PendingOrder.signal_type == signal_type,
                    PendingOrder.status == "PENDING",
                    PendingOrder.created_at >= cutoff,
                )
            ).first()
            if existing:
                logger.info(f"跳过重复信号: {symbol} {signal_type}")
                return None

            # 计算建议数量
            total_capital = get_total_capital()
            if signal_type == "SELL":
                # SELL：用实际持仓数量
                broker = get_paper_broker()
                position = broker.get_position(symbol)
                suggested_qty = position.available if position else 0
            else:
                # BUY：用资金比例计算
                suggested_qty = int(total_capital * position_size_pct / price / 100) * 100
            if suggested_qty < 100:
                suggested_qty = 100  # A 股最少 1 手

            # 风控检查（从实际 broker 获取账户状态）
            if broker_type == "paper":
                paper = get_paper_broker()
                acct = paper.get_account_info()
                positions_map = {
                    sym: {
                        "market_value": pos.market_value,
                        "avg_cost": pos.avg_cost,
                        "current_price": pos.current_price,
                    }
                    for sym, pos in paper.positions.items()
                }
                broker_info = {
                    "cash": acct["cash"],
                    "market_value": acct["market_value"],
                    "total_value": acct["total_value"],
                    "unrealized_pnl": acct["unrealized_pnl"],
                    "positions": positions_map,
                    "recent_closed_pnls": [],
                    "last_loss_date": None,
                }
            else:
                broker_info = build_broker_info(total_capital)

            # 实时读取风控配置（UI 改集中度后即时生效）
            risk_manager = RiskManager(get_effective_risk_config())
            risk_passed, risk_results = risk_manager.check_order(
                symbol=symbol,
                action=signal_type,
                quantity=suggested_qty,
                price=price,
                broker_info=broker_info,
            )
            risk_detail = [
                {"rule": r.rule_name, "passed": r.passed, "message": r.message, "severity": r.severity}
                for r in risk_results
            ]

            # 获取股票名称
            stock_name = self._get_stock_name(session, symbol)

            # 止损止盈
            stop_loss = signal.get("stop_loss", round(price * (1 - RISK_CONFIG.get("stop_loss_pct", 0.05)), 2))
            take_profit = signal.get("take_profit", round(price * (1 + RISK_CONFIG.get("take_profit_pct", 0.15)), 2))

            # 信号原因
            reasons = signal.get("reasons", [])
            if isinstance(reasons, str):
                try:
                    reasons = json.loads(reasons)
                except (json.JSONDecodeError, TypeError):
                    reasons = [{"detail": reasons}]

            # 创建订单
            order_id = f"PO-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
            expire_at = datetime.now() + timedelta(minutes=expire_minutes)

            pending = PendingOrder(
                order_id=order_id,
                symbol=symbol,
                name=stock_name,
                signal_type=signal_type,
                strategy=strategy,
                strength=strength,
                suggested_price=price,
                suggested_quantity=suggested_qty,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reasons=json.dumps(reasons, ensure_ascii=False),
                status="PENDING",
                broker_type=broker_type,
                risk_check_passed=1 if risk_passed else 0,
                risk_check_detail=json.dumps(risk_detail, ensure_ascii=False),
                scan_source=scan_source,
                expire_at=expire_at,
                order_expire_minutes=expire_minutes,
            )

            session.add(pending)
            session.commit()

            result = self._to_dict(pending)
            logger.info(f"创建待确认订单: {order_id} | {symbol} {signal_type} | 强度={strength:.2f} | 风控={'通过' if risk_passed else '未通过'}")
            return result

        except Exception as e:
            session.rollback()
            logger.error(f"创建待确认订单失败: {e}")
            return None
        finally:
            session.close()

    def confirm_order(
        self,
        order_id: str,
        broker_type: Optional[str] = None,
        override_qty: Optional[int] = None,
        override_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        确认并执行订单

        Returns:
            {"success": bool, "message": str, "order": dict}
        """
        session = get_session()
        try:
            pending = session.query(PendingOrder).filter(PendingOrder.order_id == order_id).first()
            if not pending:
                return {"success": False, "message": f"订单 {order_id} 不存在"}

            if pending.status != "PENDING":
                return {"success": False, "message": f"订单状态为 {pending.status}，无法确认"}

            # 检查过期
            if pending.expire_at and datetime.now() > pending.expire_at:
                pending.status = "EXPIRED"
                session.commit()
                return {"success": False, "message": "订单已过期"}

            # 使用参数或原始值
            exec_qty = override_qty or pending.suggested_quantity
            exec_price = override_price or pending.suggested_price
            exec_broker = broker_type or pending.broker_type

            # 再次风控检查（从实际 broker 获取账户状态）
            if exec_broker == "paper":
                paper = get_paper_broker()
                acct = paper.get_account_info()
                positions_map = {
                    sym: {
                        "market_value": pos.market_value,
                        "avg_cost": pos.avg_cost,
                        "current_price": pos.current_price,
                    }
                    for sym, pos in paper.positions.items()
                }
                broker_info = {
                    "cash": acct["cash"],
                    "market_value": acct["market_value"],
                    "total_value": acct["total_value"],
                    "unrealized_pnl": acct["unrealized_pnl"],
                    "positions": positions_map,
                    "recent_closed_pnls": [],
                    "last_loss_date": None,
                }
            else:
                total_capital = get_total_capital()
                broker_info = build_broker_info(total_capital)

            # 实时读取风控配置（UI 改集中度后即时生效）
            risk_manager = RiskManager(get_effective_risk_config())
            risk_passed, risk_results = risk_manager.check_order(
                symbol=pending.symbol,
                action=pending.signal_type,
                quantity=exec_qty,
                price=exec_price,
                broker_info=broker_info,
            )

            if not risk_passed:
                failed_rules = [r.message for r in risk_results if not r.passed]
                pending.status = "FAILED"
                pending.reject_reason = f"风控未通过: {'; '.join(failed_rules)}"
                session.commit()
                return {
                    "success": False,
                    "message": f"风控检查未通过: {'; '.join(failed_rules)}",
                    "order": self._to_dict(pending),
                }

            # 更新状态为执行中
            pending.status = "EXECUTING"
            pending.confirmed_at = datetime.now()
            session.commit()

            # 执行交易
            try:
                if exec_broker == "paper":
                    broker_order = self._execute_paper(pending.symbol, pending.signal_type, exec_qty, exec_price)
                elif exec_broker == "easytrader":
                    broker_order = self._execute_easytrader(pending.symbol, pending.signal_type, exec_qty, exec_price)
                else:
                    raise ValueError(f"不支持的券商类型: {exec_broker}")

                if broker_order.status == OrderStatus.FILLED:
                    pending.status = "FILLED"
                    pending.actual_price = broker_order.filled_price
                    pending.actual_quantity = broker_order.filled_quantity
                    pending.commission = broker_order.commission

                    # 写入 ManualTrade 表（与 Portfolio 兼容）
                    self._save_manual_trade(session, pending, broker_order)

                    logger.info(f"订单执行成功: {order_id} | {pending.symbol} {pending.signal_type} {exec_qty}股 @ {broker_order.filled_price:.2f}")
                elif broker_order.status == OrderStatus.SUBMITTED:
                    pending.status = "FILLED"  # easytrader 提交即视为成功
                    pending.actual_price = exec_price
                    pending.actual_quantity = exec_qty
                    self._save_manual_trade(session, pending, broker_order)
                    logger.info(f"订单已提交: {order_id}")
                else:
                    pending.status = "FAILED"
                    pending.reject_reason = broker_order.error_msg or "执行失败"
                    logger.warning(f"订单执行失败: {order_id} | {broker_order.error_msg}")

            except Exception as e:
                pending.status = "FAILED"
                pending.reject_reason = str(e)
                logger.error(f"订单执行异常: {order_id} | {e}")

            session.commit()
            return {
                "success": pending.status == "FILLED",
                "message": "执行成功" if pending.status == "FILLED" else (pending.reject_reason or "执行失败"),
                "order": self._to_dict(pending),
            }

        except Exception as e:
            session.rollback()
            logger.error(f"确认订单失败: {e}")
            return {"success": False, "message": str(e)}
        finally:
            session.close()

    def reject_order(self, order_id: str, reason: str = "") -> Dict[str, Any]:
        """拒绝订单"""
        session = get_session()
        try:
            pending = session.query(PendingOrder).filter(PendingOrder.order_id == order_id).first()
            if not pending:
                return {"success": False, "message": f"订单 {order_id} 不存在"}
            if pending.status != "PENDING":
                return {"success": False, "message": f"订单状态为 {pending.status}，无法拒绝"}

            pending.status = "REJECTED"
            pending.reject_reason = reason or "用户手动拒绝"
            session.commit()
            logger.info(f"订单已拒绝: {order_id} | 原因: {pending.reject_reason}")
            return {"success": True, "message": "订单已拒绝", "order": self._to_dict(pending)}
        except Exception as e:
            session.rollback()
            logger.error(f"拒绝订单失败: {e}")
            return {"success": False, "message": str(e)}
        finally:
            session.close()

    def expire_orders(self) -> int:
        """清理过期的待确认订单，返回过期数量"""
        session = get_session()
        try:
            now = datetime.now()
            expired = session.query(PendingOrder).filter(
                and_(
                    PendingOrder.status == "PENDING",
                    PendingOrder.expire_at <= now,
                )
            ).all()

            count = 0
            for order in expired:
                order.status = "EXPIRED"
                count += 1

            if count > 0:
                session.commit()
                logger.info(f"清理了 {count} 个过期订单")

            return count
        except Exception as e:
            session.rollback()
            logger.error(f"清理过期订单失败: {e}")
            return 0
        finally:
            session.close()

    def get_pending_orders(
        self,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """查询待确认订单列表"""
        session = get_session()
        try:
            query = session.query(PendingOrder)

            if status:
                query = query.filter(PendingOrder.status == status.upper())
            if symbol:
                query = query.filter(PendingOrder.symbol == symbol)

            orders = query.order_by(PendingOrder.created_at.desc()).limit(limit).all()
            return [self._to_dict(o) for o in orders]
        except Exception as e:
            logger.error(f"查询待确认订单失败: {e}")
            return []
        finally:
            session.close()

    def get_order_detail(self, order_id: str) -> Optional[Dict]:
        """查询单个订单详情"""
        session = get_session()
        try:
            pending = session.query(PendingOrder).filter(PendingOrder.order_id == order_id).first()
            return self._to_dict(pending) if pending else None
        except Exception as e:
            logger.error(f"查询订单详情失败: {e}")
            return None
        finally:
            session.close()

    def get_statistics(self) -> Dict[str, Any]:
        """获取今日统计"""
        session = get_session()
        try:
            today_start = datetime.combine(date.today(), datetime.min.time())

            total = session.query(PendingOrder).filter(PendingOrder.created_at >= today_start).count()
            pending = session.query(PendingOrder).filter(
                and_(PendingOrder.created_at >= today_start, PendingOrder.status == "PENDING")
            ).count()
            confirmed = session.query(PendingOrder).filter(
                and_(PendingOrder.created_at >= today_start, PendingOrder.status == "FILLED")
            ).count()
            rejected = session.query(PendingOrder).filter(
                and_(PendingOrder.created_at >= today_start, PendingOrder.status == "REJECTED")
            ).count()
            expired = session.query(PendingOrder).filter(
                and_(PendingOrder.created_at >= today_start, PendingOrder.status == "EXPIRED")
            ).count()
            failed = session.query(PendingOrder).filter(
                and_(PendingOrder.created_at >= today_start, PendingOrder.status == "FAILED")
            ).count()

            return {
                "today": {
                    "total": total,
                    "pending": pending,
                    "filled": confirmed,
                    "rejected": rejected,
                    "expired": expired,
                    "failed": failed,
                    "confirm_rate": round(confirmed / total * 100, 1) if total > 0 else 0,
                }
            }
        except Exception as e:
            logger.error(f"获取统计失败: {e}")
            return {"today": {"total": 0, "pending": 0, "filled": 0, "rejected": 0, "expired": 0, "failed": 0, "confirm_rate": 0}}
        finally:
            session.close()

    # -------- 内部方法 --------

    def _execute_paper(self, symbol: str, action: str, quantity: int, price: float):
        """通过 PaperBroker 执行"""
        broker = get_paper_broker()
        broker.update_market_price(symbol, price)
        return broker.submit_order(symbol, action, quantity, price)

    def _execute_easytrader(self, symbol: str, action: str, quantity: int, price: float):
        """通过 EasyTrader 执行"""
        from trading_engine.brokers.easytrader_broker import EasyTraderBroker
        broker = EasyTraderBroker()
        if not broker.connect():
            from trading_engine.brokers.base import BrokerOrder
            return BrokerOrder(
                order_id="",
                symbol=symbol,
                action=action,
                quantity=quantity,
                status=OrderStatus.FAILED,
                error_msg="EasyTrader 连接失败",
            )
        return broker.submit_order(symbol, action, quantity, price)

    def _save_manual_trade(self, session, pending: PendingOrder, broker_order):
        """将成交记录写入 ManualTrade 表"""
        exec_price = pending.actual_price or pending.suggested_price
        exec_qty = pending.actual_quantity or pending.suggested_quantity
        commission = pending.commission or 0

        trade = ManualTrade(
            symbol=pending.symbol,
            name=pending.name,
            side=pending.signal_type,
            price=exec_price,
            quantity=exec_qty,
            amount=exec_price * exec_qty,
            commission=commission,
            trade_date=date.today(),
            note=f"[自动化] 策略={pending.strategy}, 强度={pending.strength:.2f}, 来源={pending.scan_source}, 订单号={pending.order_id}",
            source_type="automation",
            pending_order_id=pending.order_id,
            ai_strategy=pending.strategy,
            ai_stop_loss=pending.stop_loss,
            ai_take_profit=pending.take_profit,
            ai_composite_score=pending.strength,
        )
        session.add(trade)
        session.flush()  # 获取 trade.id

        # SELL 交易自动生成已平仓记录
        if pending.signal_type == "SELL":
            try:
                closed_svc = ClosedTradeService()
                closed_svc.generate_closed_trade_for_sell(trade.id)
            except Exception as e:
                logger.warning(f"自动化 SELL 生成已平仓记录失败: {e}")

    def _get_stock_name(self, session, symbol: str) -> str:
        """获取股票名称"""
        try:
            info = session.query(StockInfo).filter(StockInfo.symbol == symbol).first()
            return info.name if info else symbol
        except Exception:
            return symbol

    @staticmethod
    def _to_dict(order: PendingOrder) -> Dict[str, Any]:
        """PendingOrder 转字典"""
        def _parse_json(text):
            if not text:
                return []
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return []

        return {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "name": order.name,
            "signal_type": order.signal_type,
            "strategy": order.strategy,
            "strength": order.strength,
            "suggested_price": order.suggested_price,
            "suggested_quantity": order.suggested_quantity,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "reasons": _parse_json(order.reasons),
            "status": order.status,
            "broker_type": order.broker_type,
            "actual_price": order.actual_price,
            "actual_quantity": order.actual_quantity,
            "commission": order.commission,
            "reject_reason": order.reject_reason,
            "risk_check_passed": bool(order.risk_check_passed),
            "risk_check_detail": _parse_json(order.risk_check_detail),
            "scan_source": order.scan_source,
            "expire_at": order.expire_at.isoformat() if order.expire_at else None,
            "order_expire_minutes": order.order_expire_minutes,
            "confirmed_at": order.confirmed_at.isoformat() if order.confirmed_at else None,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        }
