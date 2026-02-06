"""
数据清洗器
"""
import pandas as pd
import numpy as np
from loguru import logger


class DataCleaner:
    """数据清洗器"""

    @staticmethod
    def remove_duplicates(df: pd.DataFrame) -> pd.DataFrame:
        """去重（保留最新）"""
        if df.empty or "date" not in df.columns:
            return df

        before_count = len(df)
        df = df.drop_duplicates(subset=["date"], keep="last")
        after_count = len(df)

        if before_count != after_count:
            logger.info(f"去重: {before_count} -> {after_count} (移除 {before_count - after_count} 条)")

        return df

    @staticmethod
    def fill_missing_values(df: pd.DataFrame, method: str = "ffill") -> pd.DataFrame:
        """填充缺失值"""
        if df.empty:
            return df

        before_na = df.isna().sum().sum()

        if method == "ffill":
            # 前向填充（pandas 3.0+ 兼容）
            df = df.ffill()
        elif method == "bfill":
            # 后向填充（pandas 3.0+ 兼容）
            df = df.bfill()
        elif method == "interpolate":
            # 线性插值（仅对数值列）
            numeric_cols = ["open", "high", "low", "close", "volume"]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = df[col].interpolate(method='linear')
        elif method == "drop":
            # 删除缺失行
            df = df.dropna()

        after_na = df.isna().sum().sum()

        if before_na != after_na:
            logger.info(f"填充缺失值: {before_na} -> {after_na}")

        return df

    @staticmethod
    def fix_outliers(df: pd.DataFrame, method: str = "clip", threshold: float = 3.0) -> pd.DataFrame:
        """异常值处理"""
        if df.empty:
            return df

        if method == "clip":
            # 使用 IQR 方法裁剪异常值
            for col in ["open", "high", "low", "close"]:
                if col not in df.columns:
                    continue

                Q1 = df[col].quantile(0.25)
                Q3 = df[col].quantile(0.75)
                IQR = Q3 - Q1
                lower = Q1 - threshold * IQR
                upper = Q3 + threshold * IQR

                # 用边界值替换异常值
                outliers = ((df[col] < lower) | (df[col] > upper)).sum()
                if outliers > 0:
                    logger.info(f"{col} 列发现 {outliers} 个异常值，已裁剪")
                    df[col] = df[col].clip(lower, upper)

        elif method == "remove":
            # 移除异常值行（使用 Z-score）
            from scipy import stats

            before_count = len(df)

            for col in ["open", "high", "low", "close"]:
                if col not in df.columns:
                    continue

                z_scores = np.abs(stats.zscore(df[col]))
                df = df[z_scores < threshold]

            after_count = len(df)

            if before_count != after_count:
                logger.info(f"移除异常值: {before_count} -> {after_count}")

        return df

    @staticmethod
    def fix_ohlc_inconsistency(df: pd.DataFrame) -> pd.DataFrame:
        """修正 OHLC 不一致"""
        if df.empty:
            return df

        required_cols = ["open", "high", "low", "close"]
        if not all(col in df.columns for col in required_cols):
            return df

        # 修正 high
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)

        # 修正 low
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)

        return df

    @staticmethod
    def clean(df: pd.DataFrame, config: dict = None) -> pd.DataFrame:
        """完整清洗流程"""
        if df.empty:
            return df

        config = config or {}

        logger.info(f"开始清洗数据: {len(df)} 行")

        # 1. 去重
        df = DataCleaner.remove_duplicates(df)

        # 2. 处理缺失值
        fill_method = config.get("fill_method", "ffill")
        df = DataCleaner.fill_missing_values(df, method=fill_method)

        # 3. 修正 OHLC 不一致
        df = DataCleaner.fix_ohlc_inconsistency(df)

        # 4. 处理异常值（可选）
        if config.get("fix_outliers", False):
            outlier_method = config.get("outlier_method", "clip")
            df = DataCleaner.fix_outliers(df, method=outlier_method)

        logger.info(f"清洗完成: {len(df)} 行")

        return df
