"""
趋势类技术指标
"""
import pandas as pd
import numpy as np
from .base import BaseIndicator


class TrendIndicators(BaseIndicator):
    """趋势指标"""

    def __init__(self):
        super().__init__("TrendIndicators")

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有趋势指标"""
        if not self.validate_data(df):
            return df

        df = self.sma(df)
        df = self.ema(df)
        df = self.macd(df)
        df = self.boll(df)

        return df

    @staticmethod
    def sma(df: pd.DataFrame, periods: list = None) -> pd.DataFrame:
        """
        简单移动平均线 (Simple Moving Average)

        Args:
            df: OHLCV 数据
            periods: 周期列表，默认 [5, 10, 20, 60, 120, 250]
        """
        if periods is None:
            periods = [5, 10, 20, 60, 120, 250]

        for period in periods:
            if len(df) >= period:
                df[f"ma{period}"] = df["close"].rolling(window=period).mean()

        return df

    @staticmethod
    def ema(df: pd.DataFrame, periods: list = None) -> pd.DataFrame:
        """
        指数移动平均线 (Exponential Moving Average)

        Args:
            df: OHLCV 数据
            periods: 周期列表，默认 [12, 26, 50]
        """
        if periods is None:
            periods = [12, 26, 50]

        for period in periods:
            if len(df) >= period:
                df[f"ema{period}"] = df["close"].ewm(span=period, adjust=False).mean()

        return df

    @staticmethod
    def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """
        MACD 指标 (Moving Average Convergence Divergence)

        Args:
            df: OHLCV 数据
            fast: 快线周期，默认 12
            slow: 慢线周期，默认 26
            signal: 信号线周期，默认 9
        """
        if len(df) < slow:
            return df

        # 计算快慢线
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()

        # MACD 线
        df["macd"] = ema_fast - ema_slow

        # 信号线
        df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()

        # 柱状图
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        return df

    @staticmethod
    def boll(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
        """
        布林带 (Bollinger Bands)

        Args:
            df: OHLCV 数据
            period: 周期，默认 20
            std_dev: 标准差倍数，默认 2.0
        """
        if len(df) < period:
            return df

        # 中轨（移动平均）
        df["boll_mid"] = df["close"].rolling(window=period).mean()

        # 标准差
        rolling_std = df["close"].rolling(window=period).std()

        # 上轨和下轨
        df["boll_upper"] = df["boll_mid"] + (rolling_std * std_dev)
        df["boll_lower"] = df["boll_mid"] - (rolling_std * std_dev)

        # 带宽（波动率指标）
        df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / df["boll_mid"]

        return df

    @staticmethod
    def dmi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        DMI/ADX 指标 (Directional Movement Index)

        Args:
            df: OHLCV 数据
            period: 周期，默认 14
        """
        if len(df) < period + 1:
            return df

        # 计算真实波幅 TR
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["close"].shift(1)),
                abs(df["low"] - df["close"].shift(1))
            )
        )

        # 计算方向移动 +DM 和 -DM
        df["plus_dm"] = np.where(
            (df["high"] - df["high"].shift(1)) > (df["low"].shift(1) - df["low"]),
            np.maximum(df["high"] - df["high"].shift(1), 0),
            0
        )

        df["minus_dm"] = np.where(
            (df["low"].shift(1) - df["low"]) > (df["high"] - df["high"].shift(1)),
            np.maximum(df["low"].shift(1) - df["low"], 0),
            0
        )

        # 平滑处理
        df["tr_smooth"] = df["tr"].rolling(window=period).sum()
        df["plus_dm_smooth"] = df["plus_dm"].rolling(window=period).sum()
        df["minus_dm_smooth"] = df["minus_dm"].rolling(window=period).sum()

        # 计算方向指标（避免 tr_smooth 为 0 时除零）
        df["plus_di"] = np.where(df["tr_smooth"] != 0, 100 * df["plus_dm_smooth"] / df["tr_smooth"], 0)
        df["minus_di"] = np.where(df["tr_smooth"] != 0, 100 * df["minus_dm_smooth"] / df["tr_smooth"], 0)

        # 计算 DX 和 ADX（避免 plus_di + minus_di 为 0 时除零）
        di_sum = df["plus_di"] + df["minus_di"]
        df["dx"] = np.where(di_sum != 0, 100 * abs(df["plus_di"] - df["minus_di"]) / di_sum, 0)
        df["adx"] = df["dx"].rolling(window=period).mean()

        # 清理临时列
        df.drop(["tr", "plus_dm", "minus_dm", "tr_smooth", "plus_dm_smooth", "minus_dm_smooth", "dx"], axis=1, inplace=True)

        return df
