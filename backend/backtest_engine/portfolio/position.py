"""
持仓管理
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass
class Position:
    """持仓"""
    symbol: str                    # 股票代码
    quantity: int = 0              # 持仓数量
    available: int = 0             # 可卖数量（A股 T+1：当日买入不可卖，次日 settle_t1() 解冻）
    avg_price: float = 0.0         # 平均成本价
    current_price: float = 0.0     # 当前价格
    open_time: datetime = None     # 开仓时间
    trades: List[dict] = field(default_factory=list)  # 交易记录

    @property
    def market_value(self) -> float:
        """市值"""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        """成本"""
        return self.quantity * self.avg_price

    @property
    def unrealized_pnl(self) -> float:
        """未实现盈亏"""
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        """未实现盈亏百分比"""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100

    def update_price(self, price: float):
        """更新当前价格"""
        self.current_price = price

    def add_trade(self, quantity: int, price: float, timestamp: datetime, commission: float = 0.0):
        """
        添加交易

        Args:
            quantity: 数量（正数=买入，负数=卖出）
            price: 成交价格
            timestamp: 成交时间
            commission: 手续费
        """
        # 记录交易
        self.trades.append({
            "quantity": quantity,
            "price": price,
            "timestamp": timestamp,
            "commission": commission
        })

        # 更新持仓
        if quantity > 0:  # 买入
            # 更新平均成本价
            total_cost = self.cost_basis + (quantity * price) + commission
            self.quantity += quantity
            # T+1：当日买入不增加 available（可卖量），需下一交易日 settle_t1() 解冻
            self.avg_price = total_cost / self.quantity if self.quantity > 0 else 0.0

            # 首次建仓
            if self.open_time is None:
                self.open_time = timestamp

        elif quantity < 0:  # 卖出
            self.quantity += quantity   # quantity是负数，所以用加法
            self.available += quantity  # 可卖量同步减少

            # 清仓
            if self.quantity == 0:
                self.avg_price = 0.0
                self.available = 0
                self.open_time = None

    def __repr__(self):
        return (
            f"Position({self.symbol}, qty={self.quantity}, "
            f"avg_price={self.avg_price:.2f}, "
            f"current={self.current_price:.2f}, "
            f"pnl={self.unrealized_pnl:.2f} ({self.unrealized_pnl_pct:.2f}%))"
        )
