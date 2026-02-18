"""
技术指标计算模块
"""
import pandas as pd
import numpy as np
from typing import Dict, Any


class TechnicalIndicators:
    """技术指标计算类"""

    @staticmethod
    def calculate_ma(df: pd.DataFrame, period: int = 5, column: str = 'close') -> pd.Series:
        """
        计算移动平均线（MA）

        Args:
            df: 包含价格数据的DataFrame
            period: 周期
            column: 用于计算的列名

        Returns:
            移动平均线Series
        """
        return df[column].rolling(window=period).mean()

    @staticmethod
    def calculate_ema(df: pd.DataFrame, period: int = 12, column: str = 'close') -> pd.Series:
        """
        计算指数移动平均线（EMA）

        Args:
            df: 包含价格数据的DataFrame
            period: 周期
            column: 用于计算的列名

        Returns:
            指数移动平均线Series
        """
        return df[column].ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_macd(df: pd.DataFrame,
                      fast_period: int = 12,
                      slow_period: int = 26,
                      signal_period: int = 9) -> Dict[str, pd.Series]:
        """
        计算MACD指标

        Args:
            df: 包含价格数据的DataFrame
            fast_period: 快线周期
            slow_period: 慢线周期
            signal_period: 信号线周期

        Returns:
            包含DIF、DEA、MACD的字典
        """
        ema_fast = TechnicalIndicators.calculate_ema(df, fast_period)
        ema_slow = TechnicalIndicators.calculate_ema(df, slow_period)

        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal_period, adjust=False).mean()
        macd = (dif - dea) * 2

        return {
            'dif': dif,
            'dea': dea,
            'macd': macd
        }

    @staticmethod
    def calculate_kdj(df: pd.DataFrame,
                     period: int = 9,
                     m1: int = 3,
                     m2: int = 3) -> Dict[str, pd.Series]:
        """
        计算KDJ指标

        Args:
            df: 包含价格数据的DataFrame
            period: RSV周期
            m1: K值平滑参数
            m2: D值平滑参数

        Returns:
            包含K、D、J的字典
        """
        low_list = df['low'].rolling(window=period).min()
        high_list = df['high'].rolling(window=period).max()

        rsv = (df['close'] - low_list) / (high_list - low_list) * 100
        rsv = rsv.fillna(50)  # 初始值设为50

        k = rsv.ewm(alpha=1/m1, adjust=False).mean()
        d = k.ewm(alpha=1/m2, adjust=False).mean()
        j = 3 * k - 2 * d

        return {
            'k': k,
            'd': d,
            'j': j
        }

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        计算RSI指标（相对强弱指标）

        Args:
            df: 包含价格数据的DataFrame
            period: 周期

        Returns:
            RSI Series
        """
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    @staticmethod
    def calculate_boll(df: pd.DataFrame,
                      period: int = 20,
                      std_dev: float = 2.0) -> Dict[str, pd.Series]:
        """
        计算布林带（BOLL）

        Args:
            df: 包含价格数据的DataFrame
            period: 周期
            std_dev: 标准差倍数

        Returns:
            包含upper、middle、lower的字典
        """
        middle = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()

        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)

        return {
            'upper': upper,
            'middle': middle,
            'lower': lower
        }

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        计算平均真实波幅（ATR）

        Args:
            df: 包含OHLCV数据的DataFrame
            period: 周期

        Returns:
            ATR Series
        """
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)

        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()

        return atr

    @staticmethod
    def calculate_volume_ratio(df: pd.DataFrame, period: int = 5) -> pd.Series:
        """
        计算量比

        Args:
            df: 包含成交量数据的DataFrame
            period: 周期

        Returns:
            量比Series
        """
        avg_volume = df['volume'].rolling(window=period).mean()
        volume_ratio = df['volume'] / avg_volume

        return volume_ratio

    @staticmethod
    def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        计算所有技术指标

        Args:
            df: 包含OHLCV数据的DataFrame

        Returns:
            添加了所有指标的DataFrame
        """
        result = df.copy()

        # 移动平均线
        result['ma5'] = TechnicalIndicators.calculate_ma(df, 5)
        result['ma10'] = TechnicalIndicators.calculate_ma(df, 10)
        result['ma20'] = TechnicalIndicators.calculate_ma(df, 20)
        result['ma60'] = TechnicalIndicators.calculate_ma(df, 60)

        # MACD
        macd_dict = TechnicalIndicators.calculate_macd(df)
        result['macd_dif'] = macd_dict['dif']
        result['macd_dea'] = macd_dict['dea']
        result['macd'] = macd_dict['macd']

        # KDJ
        kdj_dict = TechnicalIndicators.calculate_kdj(df)
        result['kdj_k'] = kdj_dict['k']
        result['kdj_d'] = kdj_dict['d']
        result['kdj_j'] = kdj_dict['j']

        # RSI
        result['rsi'] = TechnicalIndicators.calculate_rsi(df, 14)

        # 布林带
        boll_dict = TechnicalIndicators.calculate_boll(df)
        result['boll_upper'] = boll_dict['upper']
        result['boll_middle'] = boll_dict['middle']
        result['boll_lower'] = boll_dict['lower']

        # 量比
        result['volume_ratio'] = TechnicalIndicators.calculate_volume_ratio(df, 5)

        # ATR
        result['atr'] = TechnicalIndicators.calculate_atr(df, 14)

        return result
