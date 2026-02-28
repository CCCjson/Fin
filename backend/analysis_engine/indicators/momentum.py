"""
动量类技术指标
"""
import pandas as pd
import numpy as np
from .base import BaseIndicator


class MomentumIndicators(BaseIndicator):
    """动量指标"""

    def __init__(self):
        super().__init__("MomentumIndicators")

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有动量指标"""
        if not self.validate_data(df):
            return df

        df = self.rsi(df)
        df = self.kdj(df)
        df = self.cci(df)
        df = self.roc(df)

        return df

    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        相对强弱指标 (Relative Strength Index)

        Args:
            df: OHLCV 数据
            period: 周期，默认 14
        """
        if len(df) < period + 1:
            return df

        # 计算价格变化
        delta = df["close"].diff()

        # 分离涨跌
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # Wilder平滑（ewm alpha=1/period），与主流行情软件一致
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

        # 计算 RS 和 RSI
        rs = avg_gain / avg_loss
        df["rsi"] = 100 - (100 / (1 + rs))

        return df

    @staticmethod
    def kdj(df: pd.DataFrame, period: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
        """
        KDJ 随机指标 (Stochastic Oscillator)

        Args:
            df: OHLCV 数据
            period: RSV 周期，默认 9
            m1: K 值平滑周期，默认 3
            m2: D 值平滑周期，默认 3
        """
        if len(df) < period:
            return df

        # 计算 RSV (未成熟随机值)
        low_min = df["low"].rolling(window=period).min()
        high_max = df["high"].rolling(window=period).max()

        df["rsv"] = 100 * (df["close"] - low_min) / (high_max - low_min)
        df["rsv"] = df["rsv"].fillna(50)  # 初始值设为 50

        # 计算 K 值（RSV 的移动平均，alpha=1/m1 与通达信一致）
        df["kdj_k"] = df["rsv"].ewm(alpha=1 / m1, adjust=False).mean()

        # 计算 D 值（K 值的移动平均）
        df["kdj_d"] = df["kdj_k"].ewm(alpha=1 / m2, adjust=False).mean()

        # 计算 J 值
        df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

        # 清理临时列
        df.drop(["rsv"], axis=1, inplace=True)

        return df

    @staticmethod
    def cci(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """
        顺势指标 (Commodity Channel Index)

        Args:
            df: OHLCV 数据
            period: 周期，默认 20
        """
        if len(df) < period:
            return df

        # 计算典型价格 (Typical Price)
        df["tp"] = (df["high"] + df["low"] + df["close"]) / 3

        # 计算移动平均
        ma_tp = df["tp"].rolling(window=period).mean()

        # 计算平均绝对偏差
        mad = df["tp"].rolling(window=period).apply(lambda x: np.abs(x - x.mean()).mean())

        # 计算 CCI（避免除零：mad 为 0 时 CCI 设为 0）
        denominator = 0.015 * mad
        df["cci"] = np.where(denominator != 0, (df["tp"] - ma_tp) / denominator, 0)

        # 清理临时列
        df.drop(["tp"], axis=1, inplace=True)

        return df

    @staticmethod
    def roc(df: pd.DataFrame, period: int = 12) -> pd.DataFrame:
        """
        变动率指标 (Rate of Change)

        Args:
            df: OHLCV 数据
            period: 周期，默认 12
        """
        if len(df) < period + 1:
            return df

        # ROC = (当前价格 - N日前价格) / N日前价格 * 100
        df["roc"] = 100 * (df["close"] - df["close"].shift(period)) / df["close"].shift(period)

        return df

    @staticmethod
    def willr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        威廉指标 (Williams %R)

        Args:
            df: OHLCV 数据
            period: 周期，默认 14
        """
        if len(df) < period:
            return df

        # 计算周期内最高价和最低价
        high_max = df["high"].rolling(window=period).max()
        low_min = df["low"].rolling(window=period).min()

        # 计算 Williams %R
        df["willr"] = -100 * (high_max - df["close"]) / (high_max - low_min)

        return df
