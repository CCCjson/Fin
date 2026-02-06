"""
MACD策略
"""
from typing import List
from datetime import datetime

from .base import BaseStrategy, StrategyContext
from ..portfolio import Order, OrderType


class MACDStrategy(BaseStrategy):
    """MACD策略 - DIF上穿DEA买入，下穿卖出"""

    def __init__(
        self,
        position_size: float = 0.95
    ):
        """
        初始化MACD策略

        Args:
            position_size: 仓位比例 (0-1)，默认 0.95
        """
        super().__init__(
            name="MACDStrategy",
            params={
                "position_size": position_size
            }
        )
        self.position_size = position_size

    def generate_signals(self, context: StrategyContext) -> List[Order]:
        """生成交易信号"""
        orders = []

        # 检查数据是否足够
        if len(context.data) < 30:
            return orders

        # 检查是否有MACD指标
        if 'macd_dif' not in context.data.columns or 'macd_dea' not in context.data.columns:
            return orders

        # 当前和前一周期的MACD值
        current_dif = context.data['macd_dif'].iloc[-1]
        current_dea = context.data['macd_dea'].iloc[-1]
        prev_dif = context.data['macd_dif'].iloc[-2]
        prev_dea = context.data['macd_dea'].iloc[-2]

        # 金叉：DIF上穿DEA，且当前无持仓
        if prev_dif <= prev_dea and current_dif > current_dea:
            if context.position_quantity == 0:
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

        # 死叉：DIF下穿DEA，且当前有持仓
        elif prev_dif >= prev_dea and current_dif < current_dea:
            if context.position_quantity > 0:
                orders.append(Order(
                    symbol=context.symbol,
                    quantity=-context.position_quantity,
                    order_type=OrderType.MARKET,
                    timestamp=context.current_time
                ))

        return orders
