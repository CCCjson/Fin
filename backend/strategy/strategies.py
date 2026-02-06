"""
具体策略实现
"""
from typing import List
import pandas as pd
import numpy as np
from .base_strategy import BaseStrategy, Signal


class MACrossStrategy(BaseStrategy):
    """均线交叉策略"""

    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        super().__init__(
            name="MA_CROSS",
            params={'fast_period': fast_period, 'slow_period': slow_period}
        )
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        """生成均线交叉信号"""
        signals = []

        if len(df) < self.slow_period + 1:
            return signals

        # 获取最新数据
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        ma_fast_col = f'ma{self.fast_period}'
        ma_slow_col = f'ma{self.slow_period}'

        if ma_fast_col not in df.columns or ma_slow_col not in df.columns:
            return signals

        ma_fast = latest[ma_fast_col]
        ma_slow = latest[ma_slow_col]
        prev_ma_fast = prev[ma_fast_col]
        prev_ma_slow = prev[ma_slow_col]

        # 检查是否有NaN
        if pd.isna(ma_fast) or pd.isna(ma_slow):
            return signals

        # 金叉：快线上穿慢线
        if prev_ma_fast <= prev_ma_slow and ma_fast > ma_slow:
            reasons = [
                f"{self.fast_period}日均线上穿{self.slow_period}日均线（金叉）",
                f"当前价格: ¥{latest['close']:.2f}"
            ]

            # 判断成交量是否放大
            volume_increase = False
            if 'volume_ratio' in df.columns and latest['volume_ratio'] > 1.5:
                volume_increase = True
                reasons.append(f"成交量放大（量比: {latest['volume_ratio']:.2f}）")

            # 计算信号强度
            conditions = [
                ma_fast > ma_slow,  # 金叉
                latest['close'] > ma_fast,  # 价格在快线上方
                volume_increase  # 成交量放大
            ]
            strength = self._calculate_strength(conditions)

            # 计算止损止盈
            stop_loss = latest['close'] * 0.95  # 5%止损
            take_profit = latest['close'] * 1.10  # 10%止盈

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size="50%"
            )
            signals.append(signal)

        # 死叉：快线下穿慢线
        elif prev_ma_fast >= prev_ma_slow and ma_fast < ma_slow:
            reasons = [
                f"{self.fast_period}日均线下穿{self.slow_period}日均线（死叉）",
                f"当前价格: ¥{latest['close']:.2f}"
            ]

            conditions = [
                ma_fast < ma_slow,  # 死叉
                latest['close'] < ma_fast  # 价格在快线下方
            ]
            strength = self._calculate_strength(conditions)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='SELL',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close']
            )
            signals.append(signal)

        return signals


class MACDStrategy(BaseStrategy):
    """MACD策略"""

    def __init__(self):
        super().__init__(name="MACD")

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        """生成MACD信号"""
        signals = []

        if len(df) < 30:
            return signals

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if 'macd_dif' not in df.columns or 'macd_dea' not in df.columns:
            return signals

        dif = latest['macd_dif']
        dea = latest['macd_dea']
        prev_dif = prev['macd_dif']
        prev_dea = prev['macd_dea']
        macd_bar = latest['macd']

        if pd.isna(dif) or pd.isna(dea):
            return signals

        # DIF上穿DEA（金叉）
        if prev_dif <= prev_dea and dif > dea:
            reasons = [
                "MACD金叉（DIF上穿DEA）",
                f"当前价格: ¥{latest['close']:.2f}",
                f"MACD柱: {macd_bar:.4f}"
            ]

            # 判断是否在零轴上方
            above_zero = dif > 0 and dea > 0
            if above_zero:
                reasons.append("MACD在零轴上方（强势区）")

            # 判断柱状图是否由绿转红
            bar_turning_red = macd_bar > 0
            if bar_turning_red:
                reasons.append("MACD柱由绿转红")

            conditions = [
                dif > dea,
                above_zero,
                bar_turning_red
            ]
            strength = self._calculate_strength(conditions)

            stop_loss = latest['close'] * 0.95
            take_profit = latest['close'] * 1.12

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size="40%"
            )
            signals.append(signal)

        # DIF下穿DEA（死叉）
        elif prev_dif >= prev_dea and dif < dea:
            reasons = [
                "MACD死叉（DIF下穿DEA）",
                f"当前价格: ¥{latest['close']:.2f}"
            ]

            conditions = [
                dif < dea,
                macd_bar < 0
            ]
            strength = self._calculate_strength(conditions)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='SELL',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close']
            )
            signals.append(signal)

        return signals


class KDJStrategy(BaseStrategy):
    """KDJ策略"""

    def __init__(self):
        super().__init__(name="KDJ")

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        """生成KDJ信号"""
        signals = []

        if len(df) < 20:
            return signals

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if 'kdj_k' not in df.columns or 'kdj_d' not in df.columns:
            return signals

        k = latest['kdj_k']
        d = latest['kdj_d']
        j = latest['kdj_j']
        prev_k = prev['kdj_k']
        prev_d = prev['kdj_d']

        if pd.isna(k) or pd.isna(d):
            return signals

        # K线上穿D线且在超卖区
        if prev_k <= prev_d and k > d and k < 30:
            reasons = [
                "KDJ金叉（K线上穿D线）",
                f"当前位置: K={k:.2f}, D={d:.2f}, J={j:.2f}",
                "处于超卖区（K<30）"
            ]

            conditions = [
                k > d,
                k < 30,
                j < 20
            ]
            strength = self._calculate_strength(conditions)

            stop_loss = latest['close'] * 0.94
            take_profit = latest['close'] * 1.08

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size="30%"
            )
            signals.append(signal)

        # K线下穿D线且在超买区
        elif prev_k >= prev_d and k < d and k > 70:
            reasons = [
                "KDJ死叉（K线下穿D线）",
                f"当前位置: K={k:.2f}, D={d:.2f}, J={j:.2f}",
                "处于超买区（K>70）"
            ]

            conditions = [
                k < d,
                k > 70,
                j > 80
            ]
            strength = self._calculate_strength(conditions)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='SELL',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close']
            )
            signals.append(signal)

        return signals


class RSIStrategy(BaseStrategy):
    """RSI策略"""

    def __init__(self, oversold: int = 30, overbought: int = 70):
        super().__init__(
            name="RSI",
            params={'oversold': oversold, 'overbought': overbought}
        )
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        """生成RSI信号"""
        signals = []

        if len(df) < 20:
            return signals

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if 'rsi' not in df.columns:
            return signals

        rsi = latest['rsi']
        prev_rsi = prev['rsi']

        if pd.isna(rsi):
            return signals

        # RSI从超卖区回升
        if prev_rsi < self.oversold and rsi > self.oversold:
            reasons = [
                f"RSI从超卖区回升（RSI={rsi:.2f}）",
                "市场可能超跌反弹"
            ]

            conditions = [
                rsi > self.oversold,
                rsi < 50
            ]
            strength = self._calculate_strength(conditions)

            stop_loss = latest['close'] * 0.96
            take_profit = latest['close'] * 1.08

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size="35%"
            )
            signals.append(signal)

        # RSI从超买区回落
        elif prev_rsi > self.overbought and rsi < self.overbought:
            reasons = [
                f"RSI从超买区回落（RSI={rsi:.2f}）",
                "市场可能过热回调"
            ]

            conditions = [
                rsi < self.overbought,
                rsi > 50
            ]
            strength = self._calculate_strength(conditions)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='SELL',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close']
            )
            signals.append(signal)

        return signals
