"""
RSI策略
"""
from typing import List
from datetime import datetime

from .base import BaseStrategy, StrategyContext
from ..portfolio import Order, OrderType


class RSIStrategy(BaseStrategy):
    """RSI策略 - 超卖买入，超买卖出"""

    def __init__(
        self,
        oversold: float = 30.0,
        overbought: float = 70.0,
        position_size: float = 0.95
    ):
        """
        初始化RSI策略

        Args:
            oversold: 超卖阈值
            overbought: 超买阈值
            position_size: 仓位比例 (0-1)
        """
        super().__init__(
            name="RSIStrategy",
            params={
                "oversold": oversold,
                "overbought": overbought,
                "position_size": position_size
            }
        )
        self.oversold = oversold
        self.overbought = overbought
        self.position_size = position_size

    def generate_signals(self, context: StrategyContext) -> List[Order]:
        """生成交易信号"""
        orders = []

        # 检查数据是否足够
        if len(context.data) < 20:
            return orders

        # 检查是否有RSI指标
        if 'rsi' not in context.data.columns:
            return orders

        # 当前和前一周期的RSI值
        current_rsi = context.data['rsi'].iloc[-1]
        prev_rsi = context.data['rsi'].iloc[-2]

        # 从超卖区反转：RSI从低于超卖线上穿超卖线
        if prev_rsi < self.oversold and current_rsi >= self.oversold:
            if context.position_quantity == 0:
                available_cash = context.cash * self.position_size
                quantity = int(available_cash / context.current_price / 100) * 100

                if quantity > 0:
                    orders.append(Order(
                        symbol=context.symbol,
                        quantity=quantity,
                        order_type=OrderType.MARKET,
                        timestamp=context.current_time
                    ))

        # 从超买区反转：RSI从高于超买线下穿超买线
        elif prev_rsi > self.overbought and current_rsi <= self.overbought:
            if context.position_quantity > 0:
                orders.append(Order(
                    symbol=context.symbol,
                    quantity=-context.position_quantity,
                    order_type=OrderType.MARKET,
                    timestamp=context.current_time
                ))

        return orders
