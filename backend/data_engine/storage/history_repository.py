"""
历史记录Repository - 管理信号、回测、订单、交易记录
"""
from typing import List, Dict, Optional
from datetime import datetime, date
import json
from sqlalchemy.orm import Session
from loguru import logger

from .models import Signal, BacktestTask, BacktestResult, Order, Trade
from .database import get_session


class HistoryRepository:
    """历史记录数据访问层"""

    def __init__(self):
        self.session = get_session()

    # ==================== 信号管理 ====================

    def save_signal(
        self,
        symbol: str,
        date: date,
        signal_type: str,
        strength: float,
        price: float,
        strategy: str = None,
        reasons: List[str] = None,
        **kwargs
    ) -> Optional[Signal]:
        """
        保存交易信号（增量添加，避免重复）

        如果同一天、同一股票、同一策略的信号已存在，则跳过
        """
        # 检查是否已存在相同信号
        existing = self.session.query(Signal).filter(
            Signal.symbol == symbol,
            Signal.date == date,
            Signal.signal_type == signal_type.upper(),
            Signal.strategy == strategy
        ).first()

        if existing:
            logger.debug(f"信号已存在，跳过: {symbol} {date} {signal_type} {strategy}")
            return None

        signal_id = f"{symbol}_{date}_{signal_type}_{strategy}_{datetime.now().strftime('%H%M%S%f')}"

        signal = Signal(
            symbol=symbol,
            date=date,
            signal_type=signal_type.upper(),  # 确保大写
            strength=strength,
            price=price,
            strategy=strategy,
            signal_id=signal_id,
            reasons=json.dumps(reasons) if reasons else None,
            entry_price=kwargs.get('entry_price'),
            stop_loss=kwargs.get('stop_loss'),
            take_profit=kwargs.get('take_profit'),
            position_size=kwargs.get('position_size')
        )

        self.session.add(signal)
        self.session.commit()
        logger.info(f"信号已保存: {signal_id}")
        return signal

    def get_signals(
        self,
        symbol: str = None,
        start_date: date = None,
        end_date: date = None,
        signal_type: str = None,
        strategy: str = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Signal]:
        """查询交易信号"""
        query = self._build_signal_query(symbol, start_date, end_date, signal_type, strategy)
        return query.order_by(Signal.date.desc(), Signal.id.desc()).offset(offset).limit(limit).all()

    def count_signals(
        self,
        symbol: str = None,
        start_date: date = None,
        end_date: date = None,
        signal_type: str = None,
        strategy: str = None,
    ) -> int:
        """统计信号总数"""
        query = self._build_signal_query(symbol, start_date, end_date, signal_type, strategy)
        return query.count()

    def _build_signal_query(self, symbol, start_date, end_date, signal_type, strategy):
        """构建信号查询条件"""
        query = self.session.query(Signal)
        if symbol:
            query = query.filter(Signal.symbol == symbol)
        if start_date:
            query = query.filter(Signal.date >= start_date)
        if end_date:
            query = query.filter(Signal.date <= end_date)
        if signal_type:
            query = query.filter(Signal.signal_type == signal_type)
        if strategy:
            query = query.filter(Signal.strategy == strategy)
        return query

    def get_signal_statistics(self, symbol: str = None, days: int = None) -> Dict:
        """
        获取信号统计（SQL 聚合，不加载全部记录到内存）

        Args:
            symbol: 股票代码（可选）
            days: 统计天数（可选，None 表示统计所有信号）
        """
        from sqlalchemy import func, case as sql_case

        query = self.session.query(
            func.count(Signal.id).label('total'),
            func.sum(sql_case((Signal.signal_type == 'BUY', 1), else_=0)).label('buy_count'),
            func.sum(sql_case((Signal.signal_type == 'SELL', 1), else_=0)).label('sell_count'),
            func.avg(Signal.strength).label('avg_strength'),
        )

        if symbol:
            query = query.filter(Signal.symbol == symbol)

        if days is not None:
            from datetime import timedelta
            cutoff_date = datetime.now().date() - timedelta(days=days)
            query = query.filter(Signal.date >= cutoff_date)

        row = query.one()

        return {
            "total_signals": int(row.total or 0),
            "buy_signals": int(row.buy_count or 0),
            "sell_signals": int(row.sell_count or 0),
            "avg_strength": float(row.avg_strength or 0),
            "days": days
        }

    # ==================== 回测管理 ====================

    def save_backtest_task(
        self,
        task_id: str,
        strategy_type: str,
        symbols: List[str],
        start_date: date,
        end_date: date,
        initial_capital: float,
        strategy_params: Dict = None,
        **kwargs
    ) -> BacktestTask:
        """保存回测任务"""
        task = BacktestTask(
            task_id=task_id,
            name=kwargs.get('name'),
            status="pending",
            strategy_type=strategy_type,
            strategy_params=json.dumps(strategy_params) if strategy_params else None,
            symbols=json.dumps(symbols),
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            commission_rate=kwargs.get('commission_rate'),
            slippage_rate=kwargs.get('slippage_rate')
        )

        self.session.add(task)
        self.session.commit()
        logger.info(f"回测任务已创建: {task_id}")
        return task

    def update_backtest_status(
        self,
        task_id: str,
        status: str,
        error_message: str = None
    ):
        """更新回测任务状态"""
        task = self.session.query(BacktestTask).filter(BacktestTask.task_id == task_id).first()
        if task:
            task.status = status
            if status == "running" and not task.started_at:
                task.started_at = datetime.now()
            elif status in ["completed", "failed"]:
                task.completed_at = datetime.now()
            if error_message:
                task.error_message = error_message

            self.session.commit()
            logger.info(f"回测任务状态更新: {task_id} -> {status}")

    def save_backtest_result(
        self,
        task_id: str,
        metrics: Dict,
        daily_records: List[Dict] = None,
        trade_records: List[Dict] = None
    ) -> BacktestResult:
        """保存回测结果"""
        result = BacktestResult(
            task_id=task_id,
            total_return=metrics.get('total_return'),
            total_return_pct=metrics.get('total_return_pct'),
            annual_return=metrics.get('annual_return'),
            final_value=metrics.get('final_value'),
            max_drawdown=metrics.get('max_drawdown'),
            max_drawdown_pct=metrics.get('max_drawdown_pct'),
            volatility=metrics.get('volatility'),
            sharpe_ratio=metrics.get('sharpe_ratio'),
            sortino_ratio=metrics.get('sortino_ratio'),
            total_trades=metrics.get('total_trades'),
            winning_trades=metrics.get('winning_trades'),
            losing_trades=metrics.get('losing_trades'),
            win_rate=metrics.get('win_rate'),
            profit_factor=metrics.get('profit_factor'),
            daily_records=json.dumps(daily_records) if daily_records else None,
            trade_records=json.dumps(trade_records) if trade_records else None
        )

        self.session.add(result)
        self.session.commit()
        logger.info(f"回测结果已保存: {task_id}")
        return result

    def get_backtest_tasks(
        self,
        status: str = None,
        strategy_type: str = None,
        limit: int = 50
    ) -> List[BacktestTask]:
        """查询回测任务"""
        query = self.session.query(BacktestTask)

        if status:
            query = query.filter(BacktestTask.status == status)
        if strategy_type:
            query = query.filter(BacktestTask.strategy_type == strategy_type)

        return query.order_by(BacktestTask.created_at.desc()).limit(limit).all()

    def get_backtest_result(self, task_id: str) -> Optional[BacktestResult]:
        """获取回测结果"""
        return self.session.query(BacktestResult).filter(
            BacktestResult.task_id == task_id
        ).first()

    def get_backtest_comparison(self, strategy_type: str = None) -> List[Dict]:
        """对比多个回测结果"""
        query = self.session.query(BacktestResult).join(BacktestTask)

        if strategy_type:
            query = query.filter(BacktestTask.strategy_type == strategy_type)

        results = query.order_by(BacktestResult.created_at.desc()).limit(10).all()

        comparison = []
        for result in results:
            task = self.session.query(BacktestTask).filter(
                BacktestTask.task_id == result.task_id
            ).first()

            comparison.append({
                "task_id": result.task_id,
                "task_name": task.name if task else None,
                "strategy": task.strategy_type if task else None,
                "total_return_pct": result.total_return_pct,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate": result.win_rate,
                "total_trades": result.total_trades,
                "created_at": result.created_at
            })

        return comparison

    # ==================== 订单管理 ====================

    def save_order(
        self,
        order_id: str,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: int,
        status: str,
        **kwargs
    ) -> Order:
        """保存订单"""
        order = Order(
            order_id=order_id,
            account_id=account_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            status=status,
            price=kwargs.get('price'),
            stop_price=kwargs.get('stop_price'),
            filled_quantity=kwargs.get('filled_quantity', 0),
            avg_fill_price=kwargs.get('avg_fill_price', 0),
            commission=kwargs.get('commission', 0),
            strategy=kwargs.get('strategy'),
            signal_strength=kwargs.get('signal_strength'),
            reason=kwargs.get('reason'),
            risk_checked=kwargs.get('risk_checked', 0),
            rejected_reason=kwargs.get('rejected_reason')
        )

        self.session.add(order)
        self.session.commit()
        logger.debug(f"订单已保存: {order_id}")
        return order

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_quantity: int = None,
        avg_fill_price: float = None,
        commission: float = None
    ):
        """更新订单状态"""
        order = self.session.query(Order).filter(Order.order_id == order_id).first()
        if order:
            order.status = status

            if filled_quantity is not None:
                order.filled_quantity = filled_quantity
            if avg_fill_price is not None:
                order.avg_fill_price = avg_fill_price
            if commission is not None:
                order.commission = commission

            if status == "SUBMITTED" and not order.submitted_at:
                order.submitted_at = datetime.now()
            elif status == "FILLED" and not order.filled_at:
                order.filled_at = datetime.now()
            elif status == "CANCELLED" and not order.cancelled_at:
                order.cancelled_at = datetime.now()

            self.session.commit()
            logger.debug(f"订单状态更新: {order_id} -> {status}")

    def get_orders(
        self,
        account_id: str = None,
        symbol: str = None,
        status: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
        limit: int = 100
    ) -> List[Order]:
        """查询订单"""
        query = self.session.query(Order)

        if account_id:
            query = query.filter(Order.account_id == account_id)
        if symbol:
            query = query.filter(Order.symbol == symbol)
        if status:
            query = query.filter(Order.status == status)
        if start_date:
            query = query.filter(Order.created_at >= start_date)
        if end_date:
            query = query.filter(Order.created_at <= end_date)

        return query.order_by(Order.created_at.desc()).limit(limit).all()

    def get_order_statistics(self, account_id: str, days: int = 30) -> Dict:
        """获取订单统计"""
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)

        orders = self.session.query(Order).filter(
            Order.account_id == account_id,
            Order.created_at >= cutoff_date
        ).all()

        total = len(orders)
        filled = len([o for o in orders if o.status == "FILLED"])
        rejected = len([o for o in orders if o.status == "REJECTED"])
        cancelled = len([o for o in orders if o.status == "CANCELLED"])

        return {
            "total_orders": total,
            "filled": filled,
            "rejected": rejected,
            "cancelled": cancelled,
            "fill_rate": (filled / total * 100) if total > 0 else 0,
            "days": days
        }

    # ==================== 成交管理 ====================

    def save_trade(
        self,
        trade_id: str,
        order_id: str,
        account_id: str,
        symbol: str,
        direction: str,
        quantity: int,
        price: float,
        commission: float,
        amount: float,
        slippage: float = 0
    ) -> Trade:
        """保存成交记录"""
        trade = Trade(
            trade_id=trade_id,
            order_id=order_id,
            account_id=account_id,
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            price=price,
            commission=commission,
            slippage=slippage,
            amount=amount
        )

        self.session.add(trade)
        self.session.commit()
        logger.debug(f"成交记录已保存: {trade_id}")
        return trade

    def get_trades(
        self,
        account_id: str = None,
        symbol: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
        limit: int = 100
    ) -> List[Trade]:
        """查询成交记录"""
        query = self.session.query(Trade)

        if account_id:
            query = query.filter(Trade.account_id == account_id)
        if symbol:
            query = query.filter(Trade.symbol == symbol)
        if start_date:
            query = query.filter(Trade.executed_at >= start_date)
        if end_date:
            query = query.filter(Trade.executed_at <= end_date)

        return query.order_by(Trade.executed_at.desc()).limit(limit).all()

    def get_trade_statistics(self, account_id: str, days: int = 30) -> Dict:
        """获取成交统计"""
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)

        trades = self.session.query(Trade).filter(
            Trade.account_id == account_id,
            Trade.executed_at >= cutoff_date
        ).all()

        total_trades = len(trades)
        buy_trades = len([t for t in trades if t.direction == "long"])
        sell_trades = len([t for t in trades if t.direction == "short"])
        total_amount = sum(t.amount for t in trades)
        total_commission = sum(t.commission for t in trades)

        return {
            "total_trades": total_trades,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "total_amount": total_amount,
            "total_commission": total_commission,
            "avg_trade_size": total_amount / total_trades if total_trades > 0 else 0,
            "days": days
        }

    def close(self):
        """关闭数据库会话"""
        self.session.close()
