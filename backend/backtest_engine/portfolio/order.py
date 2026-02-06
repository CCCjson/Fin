"""
订单定义
"""
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"  # 市价单
    LIMIT = "limit"    # 限价单


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"      # 待处理
    FILLED = "filled"        # 已成交
    CANCELLED = "cancelled"  # 已取消
    REJECTED = "rejected"    # 已拒绝


@dataclass
class Order:
    """订单"""
    symbol: str                      # 股票代码
    quantity: int                    # 数量（正数=买入，负数=卖出）
    order_type: OrderType            # 订单类型
    timestamp: datetime              # 下单时间
    price: Optional[float] = None    # 限价单价格
    filled_price: Optional[float] = None  # 成交价格
    filled_time: Optional[datetime] = None  # 成交时间
    status: OrderStatus = OrderStatus.PENDING  # 订单状态
    commission: float = 0.0          # 手续费
    order_id: Optional[str] = None   # 订单ID

    @property
    def is_buy(self) -> bool:
        """是否为买入订单"""
        return self.quantity > 0

    @property
    def is_sell(self) -> bool:
        """是否为卖出订单"""
        return self.quantity < 0

    @property
    def value(self) -> float:
        """订单价值"""
        if self.filled_price is None:
            return 0.0
        return abs(self.quantity) * self.filled_price

    def __repr__(self):
        action = "BUY" if self.is_buy else "SELL"
        return (
            f"Order({action} {abs(self.quantity)} {self.symbol} @ "
            f"{self.filled_price or self.price or 'MARKET'}, "
            f"status={self.status.value})"
        )
