"""
技术指标基类
"""
from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any


class BaseIndicator(ABC):
    """技术指标基类"""

    def __init__(self, name: str, params: Dict[str, Any] = None):
        self.name = name
        self.params = params or {}

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算指标

        Args:
            df: 包含 OHLCV 数据的 DataFrame

        Returns:
            添加了指标列的 DataFrame
        """
        pass

    def validate_data(self, df: pd.DataFrame) -> bool:
        """验证数据是否包含必要的列"""
        required_cols = ["open", "high", "low", "close", "volume"]
        return all(col in df.columns for col in required_cols)
