"""
基于分析引擎信号的策略
"""
from typing import List

from .base import BaseStrategy, StrategyContext
from ..portfolio import Order, OrderType
from analysis_engine import AnalysisEngine
from analysis_engine.signals import SignalType


class SignalStrategy(BaseStrategy):
    """基于分析引擎信号的策略"""

    def __init__(self, position_size: float = 0.95, min_signal_strength: float = 0.6):
        """
        初始化信号策略

        Args:
            position_size: 仓位比例 (0-1)
            min_signal_strength: 最小信号强度阈值
        """
        super().__init__(
            name="SignalStrategy",
            params={
                "position_size": position_size,
                "min_signal_strength": min_signal_strength
            }
        )
        self.position_size = position_size
        self.min_signal_strength = min_signal_strength
        self.analysis_engine = AnalysisEngine()

    def generate_signals(self, context: StrategyContext) -> List[Order]:
        """生成交易信号"""
        orders = []

        # 检测交易信号
        signals = self.analysis_engine.detect_signals(context.symbol, context.data)

        if not signals:
            return orders

        # 获取最新信号
        latest_signal = signals[0]

        # 检查信号强度
        if latest_signal.strength < self.min_signal_strength:
            return orders

        # 买入信号
        if latest_signal.signal_type == SignalType.BUY and context.position_quantity == 0:
            # 计算买入数量
            available_cash = context.cash * self.position_size
            quantity = int(available_cash / context.current_price / 100) * 100

            if quantity > 0:
                orders.append(Order(
                    symbol=context.symbol,
                    quantity=quantity,
                    order_type=OrderType.MARKET,
                    timestamp=context.current_time
                ))

        # 卖出信号
        elif latest_signal.signal_type == SignalType.SELL and context.position_quantity > 0:
            orders.append(Order(
                symbol=context.symbol,
                quantity=-context.position_quantity,
                order_type=OrderType.MARKET,
                timestamp=context.current_time
            ))

        return orders
