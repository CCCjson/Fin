"""
K线形态识别
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Optional


class CandlestickPatterns:
    """K线形态识别器"""

    @staticmethod
    def detect_all(df: pd.DataFrame) -> Dict[str, List[int]]:
        """
        检测所有K线形态

        Args:
            df: OHLCV 数据

        Returns:
            形态字典，key 为形态名称，value 为出现位置的索引列表
        """
        patterns = {}

        if len(df) < 3:
            return patterns

        # 单根K线形态
        patterns["doji"] = CandlestickPatterns.detect_doji(df)
        patterns["hammer"] = CandlestickPatterns.detect_hammer(df)
        patterns["shooting_star"] = CandlestickPatterns.detect_shooting_star(df)
        patterns["marubozu"] = CandlestickPatterns.detect_marubozu(df)

        # 双根K线形态
        patterns["engulfing_bullish"] = CandlestickPatterns.detect_engulfing(df, bullish=True)
        patterns["engulfing_bearish"] = CandlestickPatterns.detect_engulfing(df, bullish=False)
        patterns["harami_bullish"] = CandlestickPatterns.detect_harami(df, bullish=True)
        patterns["harami_bearish"] = CandlestickPatterns.detect_harami(df, bullish=False)

        # 三根K线形态
        patterns["morning_star"] = CandlestickPatterns.detect_morning_star(df)
        patterns["evening_star"] = CandlestickPatterns.detect_evening_star(df)
        patterns["three_white_soldiers"] = CandlestickPatterns.detect_three_white_soldiers(df)
        patterns["three_black_crows"] = CandlestickPatterns.detect_three_black_crows(df)

        return patterns

    @staticmethod
    def detect_doji(df: pd.DataFrame) -> List[int]:
        """
        十字星 (Doji) - 开盘价和收盘价几乎相等

        Args:
            df: OHLCV 数据

        Returns:
            十字星出现位置的索引列表
        """
        body_size = abs(df["close"] - df["open"])
        total_range = df["high"] - df["low"]

        # 实体小于总波动的 10%
        is_doji = body_size / total_range < 0.1

        return df.index[is_doji].tolist()

    @staticmethod
    def detect_hammer(df: pd.DataFrame) -> List[int]:
        """
        锤子线 (Hammer) - 下影线长，实体小，几乎没有上影线

        Args:
            df: OHLCV 数据

        Returns:
            锤子线出现位置的索引列表
        """
        body_size = abs(df["close"] - df["open"])
        total_range = df["high"] - df["low"]
        lower_shadow = df[["open", "close"]].min(axis=1) - df["low"]
        upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)

        # 条件：下影线 >= 实体*2，上影线很小，实体在上部
        is_hammer = (
            (lower_shadow >= body_size * 2) &
            (upper_shadow < body_size * 0.5) &
            (body_size / total_range < 0.3) &
            (total_range > 0)
        )

        return df.index[is_hammer].tolist()

    @staticmethod
    def detect_shooting_star(df: pd.DataFrame) -> List[int]:
        """
        射击之星 (Shooting Star) - 上影线长，实体小，几乎没有下影线

        Args:
            df: OHLCV 数据

        Returns:
            射击之星出现位置的索引列表
        """
        body_size = abs(df["close"] - df["open"])
        total_range = df["high"] - df["low"]
        lower_shadow = df[["open", "close"]].min(axis=1) - df["low"]
        upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)

        # 条件：上影线 >= 实体*2，下影线很小，实体在下部
        is_shooting = (
            (upper_shadow >= body_size * 2) &
            (lower_shadow < body_size * 0.5) &
            (body_size / total_range < 0.3) &
            (total_range > 0)
        )

        return df.index[is_shooting].tolist()

    @staticmethod
    def detect_marubozu(df: pd.DataFrame) -> List[int]:
        """
        光头光脚 (Marubozu) - 几乎没有上下影线

        Args:
            df: OHLCV 数据

        Returns:
            光头光脚出现位置的索引列表
        """
        body_size = abs(df["close"] - df["open"])
        total_range = df["high"] - df["low"]

        # 实体占总波动的 90% 以上
        is_marubozu = (body_size / total_range > 0.9) & (total_range > 0)

        return df.index[is_marubozu].tolist()

    @staticmethod
    def detect_engulfing(df: pd.DataFrame, bullish: bool = True) -> List[int]:
        """
        吞没形态 (Engulfing Pattern) - 第二根K线完全吞没第一根

        Args:
            df: OHLCV 数据
            bullish: True 为看涨吞没，False 为看跌吞没

        Returns:
            吞没形态出现位置的索引列表
        """
        if len(df) < 2:
            return []

        indices = []

        for i in range(1, len(df)):
            prev_open = df.iloc[i - 1]["open"]
            prev_close = df.iloc[i - 1]["close"]
            curr_open = df.iloc[i]["open"]
            curr_close = df.iloc[i]["close"]

            if bullish:
                # 看涨吞没：前一根阴线，当前阳线，完全吞没
                if (prev_close < prev_open and
                    curr_close > curr_open and
                    curr_open < prev_close and
                    curr_close > prev_open):
                    indices.append(i)
            else:
                # 看跌吞没：前一根阳线，当前阴线，完全吞没
                if (prev_close > prev_open and
                    curr_close < curr_open and
                    curr_open > prev_close and
                    curr_close < prev_open):
                    indices.append(i)

        return indices

    @staticmethod
    def detect_harami(df: pd.DataFrame, bullish: bool = True) -> List[int]:
        """
        孕育形态 (Harami Pattern) - 第二根K线被第一根包含

        Args:
            df: OHLCV 数据
            bullish: True 为看涨孕育，False 为看跌孕育

        Returns:
            孕育形态出现位置的索引列表
        """
        if len(df) < 2:
            return []

        indices = []

        for i in range(1, len(df)):
            prev_open = df.iloc[i - 1]["open"]
            prev_close = df.iloc[i - 1]["close"]
            curr_open = df.iloc[i]["open"]
            curr_close = df.iloc[i]["close"]

            prev_high = max(prev_open, prev_close)
            prev_low = min(prev_open, prev_close)
            curr_high = max(curr_open, curr_close)
            curr_low = min(curr_open, curr_close)

            if bullish:
                # 看涨孕育：前一根大阴线，当前小阳线被包含
                if (prev_close < prev_open and
                    curr_close > curr_open and
                    curr_high < prev_high and
                    curr_low > prev_low):
                    indices.append(i)
            else:
                # 看跌孕育：前一根大阳线，当前小阴线被包含
                if (prev_close > prev_open and
                    curr_close < curr_open and
                    curr_high < prev_high and
                    curr_low > prev_low):
                    indices.append(i)

        return indices

    @staticmethod
    def detect_morning_star(df: pd.DataFrame) -> List[int]:
        """
        晨星 (Morning Star) - 底部反转形态，三根K线

        Args:
            df: OHLCV 数据

        Returns:
            晨星形态出现位置的索引列表
        """
        if len(df) < 3:
            return []

        indices = []

        for i in range(2, len(df)):
            # 第一根：大阴线
            first_open = df.iloc[i - 2]["open"]
            first_close = df.iloc[i - 2]["close"]

            # 第二根：小实体（十字星）
            second_open = df.iloc[i - 1]["open"]
            second_close = df.iloc[i - 1]["close"]
            second_high = df.iloc[i - 1]["high"]
            second_low = df.iloc[i - 1]["low"]

            # 第三根：大阳线
            third_open = df.iloc[i]["open"]
            third_close = df.iloc[i]["close"]

            first_body = abs(first_close - first_open)
            second_body = abs(second_close - second_open)
            second_range = second_high - second_low
            third_body = abs(third_close - third_open)

            if (first_close < first_open and  # 第一根阴线
                second_body / second_range < 0.3 and  # 第二根小实体
                third_close > third_open and  # 第三根阳线
                third_body > first_body * 0.5 and  # 第三根实体够大
                second_close < first_close and  # 第二根向下跳空
                third_open > second_close):  # 第三根向上跳空
                indices.append(i)

        return indices

    @staticmethod
    def detect_evening_star(df: pd.DataFrame) -> List[int]:
        """
        黄昏之星 (Evening Star) - 顶部反转形态，三根K线

        Args:
            df: OHLCV 数据

        Returns:
            黄昏之星形态出现位置的索引列表
        """
        if len(df) < 3:
            return []

        indices = []

        for i in range(2, len(df)):
            # 第一根：大阳线
            first_open = df.iloc[i - 2]["open"]
            first_close = df.iloc[i - 2]["close"]

            # 第二根：小实体（十字星）
            second_open = df.iloc[i - 1]["open"]
            second_close = df.iloc[i - 1]["close"]
            second_high = df.iloc[i - 1]["high"]
            second_low = df.iloc[i - 1]["low"]

            # 第三根：大阴线
            third_open = df.iloc[i]["open"]
            third_close = df.iloc[i]["close"]

            first_body = abs(first_close - first_open)
            second_body = abs(second_close - second_open)
            second_range = second_high - second_low
            third_body = abs(third_close - third_open)

            if (first_close > first_open and  # 第一根阳线
                second_body / second_range < 0.3 and  # 第二根小实体
                third_close < third_open and  # 第三根阴线
                third_body > first_body * 0.5 and  # 第三根实体够大
                second_close > first_close and  # 第二根向上跳空
                third_open < second_close):  # 第三根向下跳空
                indices.append(i)

        return indices

    @staticmethod
    def detect_three_white_soldiers(df: pd.DataFrame) -> List[int]:
        """
        三个白武士 (Three White Soldiers) - 连续三根阳线，逐步上涨

        Args:
            df: OHLCV 数据

        Returns:
            三个白武士形态出现位置的索引列表
        """
        if len(df) < 3:
            return []

        indices = []

        for i in range(2, len(df)):
            # 三根K线都是阳线
            is_all_bullish = all([
                df.iloc[i - 2]["close"] > df.iloc[i - 2]["open"],
                df.iloc[i - 1]["close"] > df.iloc[i - 1]["open"],
                df.iloc[i]["close"] > df.iloc[i]["open"]
            ])

            # 收盘价逐步上涨
            is_ascending = (
                df.iloc[i - 1]["close"] > df.iloc[i - 2]["close"] and
                df.iloc[i]["close"] > df.iloc[i - 1]["close"]
            )

            # 每根K线开盘在前一根实体内
            is_consistent_open = (
                df.iloc[i - 2]["open"] < df.iloc[i - 1]["open"] < df.iloc[i - 2]["close"] and
                df.iloc[i - 1]["open"] < df.iloc[i]["open"] < df.iloc[i - 1]["close"]
            )

            if is_all_bullish and is_ascending and is_consistent_open:
                indices.append(i)

        return indices

    @staticmethod
    def detect_three_black_crows(df: pd.DataFrame) -> List[int]:
        """
        三只乌鸦 (Three Black Crows) - 连续三根阴线，逐步下跌

        Args:
            df: OHLCV 数据

        Returns:
            三只乌鸦形态出现位置的索引列表
        """
        if len(df) < 3:
            return []

        indices = []

        for i in range(2, len(df)):
            # 三根K线都是阴线
            is_all_bearish = all([
                df.iloc[i - 2]["close"] < df.iloc[i - 2]["open"],
                df.iloc[i - 1]["close"] < df.iloc[i - 1]["open"],
                df.iloc[i]["close"] < df.iloc[i]["open"]
            ])

            # 收盘价逐步下跌
            is_descending = (
                df.iloc[i - 1]["close"] < df.iloc[i - 2]["close"] and
                df.iloc[i]["close"] < df.iloc[i - 1]["close"]
            )

            # 每根K线开盘在前一根实体内
            is_consistent_open = (
                df.iloc[i - 2]["close"] < df.iloc[i - 1]["open"] < df.iloc[i - 2]["open"] and
                df.iloc[i - 1]["close"] < df.iloc[i]["open"] < df.iloc[i - 1]["open"]
            )

            if is_all_bearish and is_descending and is_consistent_open:
                indices.append(i)

        return indices
