"""
均线交叉策略
"""
from typing import List
from datetime import datetime

from .base import BaseStrategy, StrategyContext
from ..portfolio import Order, OrderType


class MACrossStrategy(BaseStrategy):
    """均线交叉策略 - 金叉买入，死叉卖出"""

    def __init__(
        self,
        fast_period: int = 5,
        slow_period: int = 20,
        position_size: float = 0.95
    ):
        """
        初始化均线交叉策略

        Args:
            fast_period: 快线周期
            slow_period: 慢线周期
            position_size: 仓位比例 (0-1)，默认 0.95
        """
        super().__init__(
            name="MACrossStrategy",
            params={
                "fast_period": fast_period,
                "slow_period": slow_period,
                "position_size": position_size
            }
        )
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_size = position_size

    def generate_signals(self, context: StrategyContext) -> List[Order]:
        """生成交易信号"""
        orders = []

        # 检查数据是否足够
        if len(context.data) < self.slow_period + 1:
            return orders

        # 获取均线
        fast_col = f"ma{self.fast_period}"
        slow_col = f"ma{self.slow_period}"

        if fast_col not in context.data.columns or slow_col not in context.data.columns:
            return orders

        # 当前和前一周期的均线
        current_fast = context.data[fast_col].iloc[-1]
        current_slow = context.data[slow_col].iloc[-1]
        prev_fast = context.data[fast_col].iloc[-2]
        prev_slow = context.data[slow_col].iloc[-2]

        # 金叉：快线上穿慢线，且当前无持仓
        if prev_fast <= prev_slow and current_fast > current_slow:
            if context.position_quantity == 0:
                # 计算买入数量（按资金比例）
                available_cash = context.cash * self.position_size
                quantity = int(available_cash / context.current_price / 100) * 100  # 100股为一手

                if quantity > 0:
                    orders.append(Order(
                        symbol=context.symbol,
                        quantity=quantity,
                        order_type=OrderType.MARKET,
                        timestamp=context.current_time
                    ))

        # 死叉：快线下穿慢线，且当前有持仓
        elif prev_fast >= prev_slow and current_fast < current_slow:
            if context.position_quantity > 0:
                orders.append(Order(
                    symbol=context.symbol,
                    quantity=-context.position_quantity,  # 全部卖出
                    order_type=OrderType.MARKET,
                    timestamp=context.current_time
                ))

        return orders
