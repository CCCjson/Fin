"""
分析引擎主类
"""
import pandas as pd
from typing import List, Dict, Optional
from loguru import logger

from .indicators import (
    TrendIndicators,
    MomentumIndicators,
    VolatilityIndicators,
    VolumeIndicators
)
from .signals import SignalDetector, BaseSignal
from .patterns import CandlestickPatterns


class AnalysisEngine:
    """分析引擎 - 技术分析、信号检测、形态识别"""

    def __init__(self):
        self.trend_indicators = TrendIndicators()
        self.momentum_indicators = MomentumIndicators()
        self.volatility_indicators = VolatilityIndicators()
        self.volume_indicators = VolumeIndicators()

    def add_indicators(
        self,
        df: pd.DataFrame,
        include_trend: bool = True,
        include_momentum: bool = True,
        include_volatility: bool = True,
        include_volume: bool = True
    ) -> pd.DataFrame:
        """
        添加技术指标到数据

        Args:
            df: OHLCV 数据
            include_trend: 是否包含趋势指标
            include_momentum: 是否包含动量指标
            include_volatility: 是否包含波动率指标
            include_volume: 是否包含成交量指标

        Returns:
            添加了技术指标的 DataFrame
        """
        if df.empty:
            logger.warning("数据为空，无法计算技术指标")
            return df

        # 复制一份再算，避免就地污染调用方传入的 df（子指标器都是原地写列）
        df = df.copy()

        logger.info(f"开始计算技术指标: {len(df)} 行数据")

        try:
            # 趋势指标
            if include_trend:
                logger.debug("计算趋势指标...")
                df = self.trend_indicators.calculate(df)

            # 动量指标
            if include_momentum:
                logger.debug("计算动量指标...")
                df = self.momentum_indicators.calculate(df)

            # 波动率指标
            if include_volatility:
                logger.debug("计算波动率指标...")
                df = self.volatility_indicators.calculate(df)

            # 成交量指标
            if include_volume:
                logger.debug("计算成交量指标...")
                df = self.volume_indicators.calculate(df)

            logger.success(f"技术指标计算完成，共 {len(df.columns)} 列")
            return df

        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            raise

    def detect_signals(self, symbol: str, df: pd.DataFrame) -> List[BaseSignal]:
        """
        检测交易信号

        Args:
            symbol: 股票代码
            df: 包含技术指标的 DataFrame

        Returns:
            信号列表
        """
        if df.empty:
            logger.warning("数据为空，无法检测信号")
            return []

        logger.info(f"开始检测 {symbol} 的交易信号...")

        try:
            detector = SignalDetector(symbol)
            signals = detector.detect_all(df)

            logger.success(f"检测到 {len(signals)} 个信号")
            for signal in signals:
                logger.info(f"  {signal}")

            return signals

        except Exception as e:
            logger.error(f"检测信号失败: {e}")
            return []

    def detect_patterns(self, df: pd.DataFrame) -> Dict[str, List[int]]:
        """
        检测K线形态

        Args:
            df: OHLCV 数据

        Returns:
            形态字典
        """
        if df.empty:
            logger.warning("数据为空，无法检测形态")
            return {}

        logger.info(f"开始检测K线形态...")

        try:
            patterns = CandlestickPatterns.detect_all(df)

            # 统计形态数量
            total_patterns = sum(len(indices) for indices in patterns.values())
            logger.success(f"检测到 {total_patterns} 个形态实例")

            for pattern_name, indices in patterns.items():
                if indices:
                    logger.info(f"  {pattern_name}: {len(indices)} 个")

            return patterns

        except Exception as e:
            logger.error(f"检测形态失败: {e}")
            return {}

    def analyze(
        self,
        symbol: str,
        df: pd.DataFrame,
        detect_signals: bool = True,
        detect_patterns: bool = True
    ) -> Dict:
        """
        完整分析流程：计算指标 + 检测信号 + 识别形态

        Args:
            symbol: 股票代码
            df: OHLCV 数据
            detect_signals: 是否检测信号
            detect_patterns: 是否检测形态

        Returns:
            分析结果字典
        """
        logger.info(f"\\n{'='*80}")
        logger.info(f"开始分析 {symbol}")
        logger.info(f"{'='*80}\\n")

        result = {
            "symbol": symbol,
            "data": df,
            "signals": [],
            "patterns": {}
        }

        # 1. 添加技术指标
        df_with_indicators = self.add_indicators(df)
        result["data"] = df_with_indicators

        # 2. 检测信号
        if detect_signals:
            signals = self.detect_signals(symbol, df_with_indicators)
            result["signals"] = signals

        # 3. 识别形态
        if detect_patterns:
            patterns = self.detect_patterns(df_with_indicators)
            result["patterns"] = patterns

        logger.info(f"\\n{'='*80}")
        logger.success(f"{symbol} 分析完成")
        logger.info(f"{'='*80}\\n")

        return result
