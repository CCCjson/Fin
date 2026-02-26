"""
特征工程 — 从 OHLCV 数据构建 ML 特征矩阵
复用 analysis_engine 的技术指标
"""
import pandas as pd
import numpy as np
from loguru import logger

from analysis_engine import AnalysisEngine


class FeatureBuilder:
    """特征工程器"""

    def __init__(self):
        self.analysis = AnalysisEngine()
        self._feature_cols: list[str] = []

    @property
    def feature_columns(self) -> list[str]:
        return self._feature_cols

    def build(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        从 OHLCV DataFrame 构建完整特征矩阵。

        Args:
            df: 至少包含 date, open, high, low, close, volume 列

        Returns:
            包含所有特征列的 DataFrame（已去除 NaN 行）
        """
        if len(df) < 60:
            raise ValueError(f"数据不足 60 行（当前 {len(df)} 行），无法构建特征")

        logger.info(f"开始构建特征矩阵，原始数据 {len(df)} 行")

        feat = df.copy()

        # 1) 技术指标（复用 analysis_engine）
        feat = self.analysis.add_indicators(feat)

        # 2) 价量衍生特征
        feat = self._add_price_features(feat)

        # 3) 滞后特征
        feat = self._add_lag_features(feat)

        # 去除因指标/滞后产生的 NaN
        feat.dropna(inplace=True)
        feat.reset_index(drop=True, inplace=True)

        # 记录特征列（排除非特征列）
        exclude = {"date", "symbol", "market", "id", "created_at", "updated_at",
                   "amount", "turnover", "adjust_factor"}
        self._feature_cols = [c for c in feat.columns if c not in exclude]

        logger.info(f"特征构建完成: {len(feat)} 行, {len(self._feature_cols)} 个特征")
        return feat

    @staticmethod
    def _add_price_features(df: pd.DataFrame) -> pd.DataFrame:
        """价量衍生特征"""
        c = df["close"]

        # 收益率
        for n in [1, 3, 5, 10, 20]:
            df[f"return_{n}d"] = c.pct_change(n)

        # 振幅
        df["amplitude"] = (df["high"] - df["low"]) / df["close"].shift(1)

        # 量比
        df["volume_change"] = df["volume"].pct_change()
        for n in [5, 10, 20]:
            vol_ma = df["volume"].rolling(n).mean()
            df[f"vol_ratio_{n}d"] = df["volume"] / vol_ma.replace(0, np.nan)

        # 价格与均线的偏离度
        for col in ["ma5", "ma10", "ma20", "ma60"]:
            if col in df.columns:
                df[f"bias_{col}"] = (c - df[col]) / df[col].replace(0, np.nan)

        return df

    @staticmethod
    def _add_lag_features(df: pd.DataFrame, lags: list[int] | None = None) -> pd.DataFrame:
        """滞后特征：对 close/volume/return_1d 做 lag"""
        if lags is None:
            lags = [1, 2, 3, 5, 10]

        for lag in lags:
            df[f"close_lag{lag}"] = df["close"].shift(lag)
            df[f"volume_lag{lag}"] = df["volume"].shift(lag)
            if "return_1d" in df.columns:
                df[f"return_1d_lag{lag}"] = df["return_1d"].shift(lag)

        return df
