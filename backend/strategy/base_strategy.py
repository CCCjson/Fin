"""
策略基类
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import date


class Signal:
    """交易信号"""

    def __init__(self,
                 symbol: str,
                 date: date,
                 signal_type: str,  # BUY or SELL
                 strength: float,  # 0-1
                 price: float,
                 strategy: str,
                 reasons: List[str],
                 entry_price: Optional[float] = None,
                 stop_loss: Optional[float] = None,
                 take_profit: Optional[float] = None,
                 position_size: str = "100%"):
        self.symbol = symbol
        self.date = date
        self.signal_type = signal_type
        self.strength = strength
        self.price = price
        self.strategy = strategy
        self.reasons = reasons
        self.entry_price = entry_price or price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.position_size = position_size

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'symbol': self.symbol,
            'date': self.date,
            'signal_type': self.signal_type,
            'strength': self.strength,
            'price': self.price,
            'strategy': self.strategy,
            'reasons': self.reasons,
            'entry_price': self.entry_price,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'position_size': self.position_size
        }


class BaseStrategy(ABC):
    """策略基类"""

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        self.name = name
        self.params = params or {}

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        """
        生成交易信号

        Args:
            df: 包含OHLCV数据和技术指标的DataFrame
            symbol: 股票代码

        Returns:
            信号列表
        """
        pass

    def _calculate_strength(self, conditions: List[bool]) -> float:
        """
        根据满足的条件数量计算信号强度

        Args:
            conditions: 条件列表

        Returns:
            信号强度 (0-1)
        """
        if not conditions:
            return 0.0

        satisfied = sum(1 for c in conditions if c)
        return min(satisfied / len(conditions), 1.0)
