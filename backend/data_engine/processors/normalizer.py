"""
数据标准化器
"""
import pandas as pd
import numpy as np
from loguru import logger


class DataNormalizer:
    """数据标准化器"""

    @staticmethod
    def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """列名标准化"""
        column_mapping = {
            # 日期
            "日期": "date", "Date": "date", "trade_date": "date", "时间": "date",
            # 开盘
            "开盘": "open", "Open": "open", "open_price": "open",
            # 最高
            "最高": "high", "High": "high", "high_price": "high",
            # 最低
            "最低": "low", "Low": "low", "low_price": "low",
            # 收盘
            "收盘": "close", "Close": "close", "close_price": "close",
            # 成交量
            "成交量": "volume", "Volume": "volume", "vol": "volume",
            # 成交额
            "成交额": "amount", "Amount": "amount",
            # 换手率
            "换手率": "turnover", "Turnover": "turnover",
        }

        df = df.rename(columns=column_mapping)
        return df

    @staticmethod
    def normalize_datetime(df: pd.DataFrame) -> pd.DataFrame:
        """时间标准化"""
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df

    @staticmethod
    def normalize_numeric(df: pd.DataFrame) -> pd.DataFrame:
        """数值类型标准化"""
        numeric_columns = ["open", "high", "low", "close", "volume", "amount", "turnover"]
        for col in numeric_columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    @staticmethod
    def remove_nan(df: pd.DataFrame) -> pd.DataFrame:
        """移除 NaN 值"""
        # 移除所有列都是 NaN 的行
        df = df.dropna(how='all')

        # 对于关键列，如果有 NaN 则移除该行
        required_cols = ["open", "high", "low", "close", "volume"]
        existing_required = [col for col in required_cols if col in df.columns]

        if existing_required:
            df = df.dropna(subset=existing_required)

        return df

    @staticmethod
    def normalize(df: pd.DataFrame) -> pd.DataFrame:
        """完整标准化流程"""
        if df.empty:
            return df

        logger.debug(f"标准化前: {len(df)} 行")

        # 1. 列名标准化
        df = DataNormalizer.normalize_columns(df)

        # 2. 时间标准化
        df = DataNormalizer.normalize_datetime(df)

        # 3. 数值标准化
        df = DataNormalizer.normalize_numeric(df)

        # 4. 移除 NaN
        df = DataNormalizer.remove_nan(df)

        # 5. 按日期排序
        if "date" in df.columns:
            df = df.sort_values("date").reset_index(drop=True)

        logger.debug(f"标准化后: {len(df)} 行")

        return df
