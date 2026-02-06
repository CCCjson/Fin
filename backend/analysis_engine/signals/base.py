"""
信号基类
"""
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class SignalType(Enum):
    """信号类型"""
    BUY = "buy"          # 买入信号
    SELL = "sell"        # 卖出信号
    HOLD = "hold"        # 持有信号


@dataclass
class BaseSignal:
    """交易信号"""
    signal_type: SignalType      # 信号类型
    symbol: str                   # 股票代码
    timestamp: datetime           # 信号时间
    price: float                  # 信号价格
    strength: float               # 信号强度 (0-1)
    reason: str                   # 信号原因
    indicators: dict              # 相关指标值
    metadata: Optional[dict] = None  # 额外信息

    def __repr__(self):
        return (
            f"Signal({self.signal_type.value.upper()}, "
            f"{self.symbol}, "
            f"price={self.price:.2f}, "
            f"strength={self.strength:.2f}, "
            f"reason={self.reason})"
        )
