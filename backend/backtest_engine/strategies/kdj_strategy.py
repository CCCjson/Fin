"""
KDJ策略
"""
from typing import List
from datetime import datetime

from .base import BaseStrategy, StrategyContext
from ..portfolio import Order, OrderType


class KDJStrategy(BaseStrategy):
    """KDJ策略 - 超卖区金叉买入，超买区死叉卖出"""

    def __init__(
        self,
        oversold: float = 20.0,
        overbought: float = 80.0,
        position_size: float = 0.95
    ):
        """
        初始化KDJ策略

        Args:
            oversold: 超卖阈值
            overbought: 超买阈值
            position_size: 仓位比例 (0-1)
        """
        super().__init__(
            name="KDJStrategy",
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
        if len(context.data) < 15:
            return orders

        # 检查是否有KDJ指标
        if 'kdj_k' not in context.data.columns or 'kdj_d' not in context.data.columns:
            return orders

        # 当前和前一周期的KDJ值
        current_k = context.data['kdj_k'].iloc[-1]
        current_d = context.data['kdj_d'].iloc[-1]
        prev_k = context.data['kdj_k'].iloc[-2]
        prev_d = context.data['kdj_d'].iloc[-2]

        # 超卖区金叉：K线上穿D线，且在超卖区
        if prev_k <= prev_d and current_k > current_d and current_k < self.oversold:
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

        # 超买区死叉：K线下穿D线，且在超买区
        elif prev_k >= prev_d and current_k < current_d and current_k > self.overbought:
            if context.position_quantity > 0:
                orders.append(Order(
                    symbol=context.symbol,
                    quantity=-context.position_quantity,
                    order_type=OrderType.MARKET,
                    timestamp=context.current_time
                ))

        return orders
