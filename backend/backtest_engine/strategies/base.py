"""
策略基类
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
from typing import Optional, List

from ..portfolio import Order, OrderType


@dataclass
class StrategyContext:
    """策略上下文 - 提供给策略的当前状态信息"""
    symbol: str                    # 股票代码
    current_time: datetime         # 当前时间
    current_price: float           # 当前价格
    data: pd.DataFrame            # 历史数据（包含指标）
    cash: float                    # 可用资金
    position_quantity: int         # 当前持仓数量
    position_avg_price: float      # 持仓均价
    total_value: float             # 总资产


class BaseStrategy(ABC):
    """策略基类"""

    def __init__(self, name: str, params: dict = None):
        """
        初始化策略

        Args:
            name: 策略名称
            params: 策略参数
        """
        self.name = name
        self.params = params or {}

    @abstractmethod
    def generate_signals(self, context: StrategyContext) -> List[Order]:
        """
        生成交易信号

        Args:
            context: 策略上下文

        Returns:
            订单列表
        """
        pass

    def on_start(self):
        """回测开始时调用"""
        pass

    def on_finish(self):
        """回测结束时调用"""
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name}, params={self.params})"
