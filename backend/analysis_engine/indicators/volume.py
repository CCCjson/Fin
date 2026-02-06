"""
成交量类技术指标
"""
import pandas as pd
import numpy as np
from .base import BaseIndicator


class VolumeIndicators(BaseIndicator):
    """成交量指标"""

    def __init__(self):
        super().__init__("VolumeIndicators")

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有成交量指标"""
        if not self.validate_data(df):
            return df

        df = self.obv(df)
        df = self.volume_ma(df)
        df = self.vwap(df)
        df = self.mfi(df)

        return df

    @staticmethod
    def obv(df: pd.DataFrame) -> pd.DataFrame:
        """
        能量潮 (On-Balance Volume)

        Args:
            df: OHLCV 数据
        """
        if len(df) < 2:
            return df

        # 计算价格变化方向
        price_change = df["close"].diff()

        # OBV 计算
        obv = np.where(price_change > 0, df["volume"],
                      np.where(price_change < 0, -df["volume"], 0))

        df["obv"] = obv.cumsum()

        return df

    @staticmethod
    def volume_ma(df: pd.DataFrame, periods: list = None) -> pd.DataFrame:
        """
        成交量移动平均 (Volume Moving Average)

        Args:
            df: OHLCV 数据
            periods: 周期列表，默认 [5, 10, 20]
        """
        if periods is None:
            periods = [5, 10, 20]

        for period in periods:
            if len(df) >= period:
                df[f"volume_ma{period}"] = df["volume"].rolling(window=period).mean()

        return df

    @staticmethod
    def vwap(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """
        成交量加权平均价 (Volume Weighted Average Price)

        Args:
            df: OHLCV 数据
            period: 周期，默认 20（None 表示从头开始累计）
        """
        # 计算典型价格
        typical_price = (df["high"] + df["low"] + df["close"]) / 3

        # 累计成交量 * 典型价格
        if period is None:
            # 从头开始累计
            df["vwap"] = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()
        else:
            # 滚动周期
            if len(df) >= period:
                df["vwap"] = (
                    (typical_price * df["volume"]).rolling(window=period).sum() /
                    df["volume"].rolling(window=period).sum()
                )

        return df

    @staticmethod
    def mfi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        资金流量指标 (Money Flow Index)

        Args:
            df: OHLCV 数据
            period: 周期，默认 14
        """
        if len(df) < period + 1:
            return df

        # 计算典型价格
        typical_price = (df["high"] + df["low"] + df["close"]) / 3

        # 计算资金流量
        money_flow = typical_price * df["volume"]

        # 区分正负资金流量
        positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0)
        negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0)

        # 计算资金流量比率
        positive_mf = positive_flow.rolling(window=period).sum()
        negative_mf = negative_flow.rolling(window=period).sum()

        # 避免除零
        mfi_ratio = positive_mf / negative_mf.replace(0, 1e-10)

        # 计算 MFI
        df["mfi"] = 100 - (100 / (1 + mfi_ratio))

        return df

    @staticmethod
    def volume_ratio(df: pd.DataFrame, period: int = 5) -> pd.DataFrame:
        """
        量比 (Volume Ratio)

        Args:
            df: OHLCV 数据
            period: 周期，默认 5
        """
        if len(df) < period:
            return df

        # 量比 = 当前成交量 / 过去N日平均成交量
        df["volume_ratio"] = df["volume"] / df["volume"].rolling(window=period).mean()

        return df
