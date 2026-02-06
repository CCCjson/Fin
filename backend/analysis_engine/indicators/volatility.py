"""
波动率类技术指标
"""
import pandas as pd
import numpy as np
from .base import BaseIndicator


class VolatilityIndicators(BaseIndicator):
    """波动率指标"""

    def __init__(self):
        super().__init__("VolatilityIndicators")

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有波动率指标"""
        if not self.validate_data(df):
            return df

        df = self.atr(df)
        df = self.keltner(df)
        df = self.donchian(df)

        return df

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        真实波幅 (Average True Range)

        Args:
            df: OHLCV 数据
            period: 周期，默认 14
        """
        if len(df) < period + 1:
            return df

        # 计算真实波幅 TR
        high_low = df["high"] - df["low"]
        high_close = abs(df["high"] - df["close"].shift(1))
        low_close = abs(df["low"] - df["close"].shift(1))

        df["tr"] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

        # 计算 ATR（TR 的移动平均）
        df["atr"] = df["tr"].rolling(window=period).mean()

        # ATR 百分比（相对于价格）
        df["atr_pct"] = 100 * df["atr"] / df["close"]

        # 清理临时列
        df.drop(["tr"], axis=1, inplace=True)

        return df

    @staticmethod
    def keltner(df: pd.DataFrame, period: int = 20, multiplier: float = 2.0) -> pd.DataFrame:
        """
        肯特纳通道 (Keltner Channel)

        Args:
            df: OHLCV 数据
            period: 周期，默认 20
            multiplier: ATR 倍数，默认 2.0
        """
        if len(df) < period + 1:
            return df

        # 计算 ATR（如果还没有）
        if "atr" not in df.columns:
            df = VolatilityIndicators.atr(df, period)

        # 计算典型价格的移动平均（中轨）
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        df["keltner_mid"] = typical_price.rolling(window=period).mean()

        # 上下轨
        df["keltner_upper"] = df["keltner_mid"] + (multiplier * df["atr"])
        df["keltner_lower"] = df["keltner_mid"] - (multiplier * df["atr"])

        return df

    @staticmethod
    def donchian(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """
        唐奇安通道 (Donchian Channel)

        Args:
            df: OHLCV 数据
            period: 周期，默认 20
        """
        if len(df) < period:
            return df

        # 上轨：周期内最高价
        df["donchian_upper"] = df["high"].rolling(window=period).max()

        # 下轨：周期内最低价
        df["donchian_lower"] = df["low"].rolling(window=period).min()

        # 中轨：上下轨的平均
        df["donchian_mid"] = (df["donchian_upper"] + df["donchian_lower"]) / 2

        return df

    @staticmethod
    def historical_volatility(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """
        历史波动率 (Historical Volatility)

        Args:
            df: OHLCV 数据
            period: 周期，默认 20
        """
        if len(df) < period + 1:
            return df

        # 计算对数收益率
        log_returns = np.log(df["close"] / df["close"].shift(1))

        # 计算滚动标准差（年化波动率，假设 252 个交易日）
        df["hv"] = log_returns.rolling(window=period).std() * np.sqrt(252) * 100

        return df
