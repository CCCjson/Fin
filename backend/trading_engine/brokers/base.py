"""
券商接口基类
"""
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"          # 待提交
    SUBMITTED = "submitted"      # 已提交
    PARTIAL_FILLED = "partial"   # 部分成交
    FILLED = "filled"            # 完全成交
    CANCELLED = "cancelled"      # 已撤销
    REJECTED = "rejected"        # 已拒绝
    FAILED = "failed"            # 失败


@dataclass
class BrokerOrder:
    """券商订单"""
    order_id: str                    # 订单ID
    symbol: str                      # 股票代码
    action: str                      # 动作 (BUY/SELL)
    quantity: int                    # 数量
    price: Optional[float] = None    # 价格（None=市价单）
    filled_quantity: int = 0         # 已成交数量
    filled_price: float = 0.0        # 成交均价
    status: OrderStatus = OrderStatus.PENDING
    submit_time: Optional[datetime] = None
    filled_time: Optional[datetime] = None
    commission: float = 0.0          # 手续费
    error_msg: Optional[str] = None  # 错误信息


@dataclass
class BrokerPosition:
    """券商持仓"""
    symbol: str              # 股票代码
    quantity: int            # 持仓数量
    avg_cost: float          # 平均成本
    current_price: float     # 当前价格
    market_value: float      # 市值
    unrealized_pnl: float    # 未实现盈亏
    available: int           # 可用数量（T+1）


class BaseBroker(ABC):
    """券商接口基类"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def get_account_info(self) -> dict:
        """
        获取账户信息

        Returns:
            包含现金、总资产等信息的字典
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[BrokerPosition]:
        """获取持仓列表"""
        pass

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        """获取指定股票的持仓"""
        pass

    @abstractmethod
    def submit_order(self, symbol: str, action: str, quantity: int, price: Optional[float] = None) -> BrokerOrder:
        """
        提交订单

        Args:
            symbol: 股票代码
            action: BUY 或 SELL
            quantity: 数量
            price: 价格（None=市价单）

        Returns:
            订单对象
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """查询订单"""
        pass

    @abstractmethod
    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """获取订单列表"""
        pass

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"
