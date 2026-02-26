"""
预测模型基类
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

import pandas as pd


class BasePredictor(ABC):
    """预测模型基类"""

    def __init__(self, name: str):
        self.name = name
        self.is_trained = False
        self.val_metrics: Dict[str, float] = {}

    @abstractmethod
    def train(self, df: pd.DataFrame, feature_cols: list[str], forward_days: int) -> Dict[str, float]:
        """
        训练模型。

        Args:
            df: 包含特征和 close 列的 DataFrame
            feature_cols: 特征列名列表
            forward_days: 预测天数

        Returns:
            验证指标字典
        """

    @abstractmethod
    def predict(self, df: pd.DataFrame, feature_cols: list[str], forward_days: int) -> Dict[str, Any]:
        """
        生成预测。

        Args:
            df: 包含特征的 DataFrame（使用最后一段数据做输入）
            feature_cols: 特征列名列表
            forward_days: 预测天数

        Returns:
            预测结果字典
        """

    @abstractmethod
    def save(self, path: Path) -> None:
        """保存模型到文件"""

    @abstractmethod
    def load(self, path: Path) -> None:
        """从文件加载模型"""
