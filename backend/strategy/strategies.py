"""
具体策略实现（增强版）

改造要点：
- 新增 3 个模块级工具函数：趋势确认、量能信息、ATR 动态止损
- 4 个策略全部增强：更多条件、量能确认、趋势方向、ATR 止损
- MACD 新增底背离/顶背离检测
"""
from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np
from .base_strategy import BaseStrategy, Signal


# ======================================================================
# 模块级工具函数
# ======================================================================

def _check_trend_alignment(df: pd.DataFrame, lookback: int = 5) -> Dict:
    """
    回看 lookback 根 K 线，分析趋势方向。

    Returns:
        {
            "direction": "up" | "down" | "neutral",
            "desc": str,  # 人类可读的趋势描述
            "ma_aligned": bool,  # 均线多头/空头排列
            "higher_lows": bool,  # 低点抬升
            "lower_highs": bool,  # 高点降低
            "price_above_ma20": bool,
        }
    """
    result = {
        "direction": "neutral",
        "desc": "趋势中性",
        "ma_aligned": False,
        "higher_lows": False,
        "lower_highs": False,
        "price_above_ma20": False,
    }

    if len(df) < lookback + 1:
        return result

    latest = df.iloc[-1]
    recent = df.iloc[-lookback:]

    # 均线排列检查
    ma5 = latest.get('ma5')
    ma10 = latest.get('ma10')
    ma20 = latest.get('ma20')

    if ma5 is not None and ma10 is not None and ma20 is not None:
        if not (pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20)):
            if ma5 > ma10 > ma20:
                result["ma_aligned"] = True
                result["direction"] = "up"
                result["desc"] = "均线多头排列（MA5>MA10>MA20），趋势向上"
            elif ma5 < ma10 < ma20:
                result["direction"] = "down"
                result["desc"] = "均线空头排列（MA5<MA10<MA20），趋势向下"

    # 价格 vs MA20
    if ma20 is not None and not pd.isna(ma20):
        result["price_above_ma20"] = latest['close'] > ma20

    # 低点抬升 / 高点降低
    lows = recent['low'].values
    highs = recent['high'].values

    if len(lows) >= 3:
        rising_lows = all(lows[i] >= lows[i - 1] for i in range(1, len(lows)))
        falling_highs = all(highs[i] <= highs[i - 1] for i in range(1, len(highs)))

        result["higher_lows"] = rising_lows
        result["lower_highs"] = falling_highs

        if rising_lows and result["direction"] == "neutral":
            result["direction"] = "up"
            result["desc"] = "近期低点持续抬升，趋势偏多"
        elif falling_highs and result["direction"] == "neutral":
            result["direction"] = "down"
            result["desc"] = "近期高点持续降低，趋势偏空"

    return result


def _get_volume_info(df: pd.DataFrame) -> Dict:
    """
    提取量比信息。

    Returns:
        {
            "volume_ratio": float | None,
            "desc": str,  # 包含 "量比: X.XX" 格式，供 scorer 正则提取
            "is_amplified": bool,  # 量比 > 1.5
            "is_shrunk": bool,  # 量比 < 0.8
        }
    """
    result = {
        "volume_ratio": None,
        "desc": "量能数据不可用",
        "is_amplified": False,
        "is_shrunk": False,
    }

    if 'volume_ratio' not in df.columns or len(df) < 1:
        return result

    vr = df.iloc[-1]['volume_ratio']
    if pd.isna(vr):
        return result

    result["volume_ratio"] = float(vr)

    if vr >= 2.0:
        result["desc"] = f"量比: {vr:.2f}（显著放量）"
        result["is_amplified"] = True
    elif vr >= 1.5:
        result["desc"] = f"量比: {vr:.2f}（温和放量）"
        result["is_amplified"] = True
    elif vr >= 0.8:
        result["desc"] = f"量比: {vr:.2f}（平量）"
    else:
        result["desc"] = f"量比: {vr:.2f}（缩量）"
        result["is_shrunk"] = True

    return result


def _compute_atr_stops(
    df: pd.DataFrame,
    signal_type: str,
    sl_mult: float = 2.0,
    tp_mult: float = 3.0,
) -> Tuple[float, float, str]:
    """
    ATR 动态止损止盈。ATR 不可用时 fallback 到固定百分比。

    Args:
        df: 含 'atr' 列的 DataFrame
        signal_type: "BUY" or "SELL"
        sl_mult: 止损倍数（ATR 的几倍）
        tp_mult: 止盈倍数

    Returns:
        (stop_loss, take_profit, method_desc)
    """
    price = df.iloc[-1]['close']

    if 'atr' in df.columns and not pd.isna(df.iloc[-1]['atr']):
        atr = df.iloc[-1]['atr']
        if signal_type == 'BUY':
            sl = round(price - atr * sl_mult, 2)
            tp = round(price + atr * tp_mult, 2)
        else:
            sl = round(price + atr * sl_mult, 2)
            tp = round(price - atr * tp_mult, 2)
        method = f"ATR动态止损（ATR={atr:.2f}, 止损{sl_mult}倍ATR, 止盈{tp_mult}倍ATR）"
    else:
        # fallback 固定百分比
        if signal_type == 'BUY':
            sl = round(price * 0.95, 2)
            tp = round(price * 1.10, 2)
        else:
            sl = round(price * 1.05, 2)
            tp = round(price * 0.90, 2)
        method = "固定百分比止损（ATR不可用，5%止损/10%止盈）"

    return sl, tp, method


# ======================================================================
# 策略实现
# ======================================================================

class MACrossStrategy(BaseStrategy):
    """均线交叉策略（增强版）"""

    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        super().__init__(
            name="MA_CROSS",
            params={'fast_period': fast_period, 'slow_period': slow_period}
        )
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
        signals = []

        if len(df) < self.slow_period + 1:
            return signals

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

        if pd.isna(ma_fast) or pd.isna(ma_slow):
            return signals

        trend = _check_trend_alignment(df)
        vol = _get_volume_info(df)

        # 金叉：快线上穿慢线
        if prev_ma_fast <= prev_ma_slow and ma_fast > ma_slow:
            reasons = [
                f"{self.fast_period}日均线上穿{self.slow_period}日均线（金叉）",
                f"当前价格: ¥{latest['close']:.2f}",
                trend["desc"],
                vol["desc"],
            ]

            conditions = [
                ma_fast > ma_slow,                      # 金叉
                latest['close'] > ma_fast,              # 价格在快线上方
                vol["is_amplified"],                     # 成交量放大
                trend["direction"] == "up",             # 趋势向上
                trend["price_above_ma20"],              # 价格在 MA20 上方
                trend["higher_lows"],                   # 低点抬升
            ]
            strength = self._calculate_strength(conditions)

            sl, tp, sl_desc = _compute_atr_stops(df, 'BUY', sl_mult=2.0, tp_mult=3.0)
            reasons.append(sl_desc)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=sl,
                take_profit=tp,
                position_size="50%"
            )
            signals.append(signal)

        # 死叉：快线下穿慢线
        elif prev_ma_fast >= prev_ma_slow and ma_fast < ma_slow:
            reasons = [
                f"{self.fast_period}日均线下穿{self.slow_period}日均线（死叉）",
                f"当前价格: ¥{latest['close']:.2f}",
                trend["desc"],
                vol["desc"],
            ]

            conditions = [
                ma_fast < ma_slow,                      # 死叉
                latest['close'] < ma_fast,              # 价格在快线下方
                trend["direction"] == "down",           # 趋势向下
                trend["lower_highs"],                   # 高点降低
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
    """MACD策略（增强版：+量能确认 +趋势方向 +底背离/顶背离检测）"""

    def __init__(self):
        super().__init__(name="MACD")

    def _detect_divergence(self, df: pd.DataFrame, lookback: int = 10) -> Optional[str]:
        """
        检测 MACD 底背离/顶背离。

        底背离：价格创新低，但 MACD DIF 未创新低 → 看涨
        顶背离：价格创新高，但 MACD DIF 未创新高 → 看跌

        Returns:
            "bullish_divergence" | "bearish_divergence" | None
        """
        if len(df) < lookback + 1 or 'macd_dif' not in df.columns:
            return None

        recent = df.iloc[-lookback:]
        latest_close = df.iloc[-1]['close']
        latest_dif = df.iloc[-1]['macd_dif']

        if pd.isna(latest_dif):
            return None

        closes = recent['close'].values
        difs = recent['macd_dif'].values

        # 过滤 NaN
        valid_mask = ~np.isnan(difs)
        if valid_mask.sum() < 3:
            return None

        closes = closes[valid_mask]
        difs = difs[valid_mask]

        # 用 DIF 区间范围的 10% 作为背离阈值，避免乘法在负数时方向反转
        dif_min = difs.min()
        dif_max = difs.max()
        dif_range = dif_max - dif_min
        dif_threshold = dif_range * 0.10 if dif_range > 0 else 0.01

        # 底背离：价格在区间最低点附近，但 DIF 明显高于其最低点
        if (latest_close <= closes.min() * 1.02 and
                latest_dif > dif_min + dif_threshold):
            return "bullish_divergence"

        # 顶背离：价格在区间最高点附近，但 DIF 明显低于其最高点
        if (latest_close >= closes.max() * 0.98 and
                latest_dif < dif_max - dif_threshold):
            return "bearish_divergence"

        return None

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
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

        trend = _check_trend_alignment(df)
        vol = _get_volume_info(df)
        divergence = self._detect_divergence(df)

        # DIF上穿DEA（金叉）
        if prev_dif <= prev_dea and dif > dea:
            reasons = [
                "MACD金叉（DIF上穿DEA）",
                f"当前价格: ¥{latest['close']:.2f}",
                f"MACD柱: {macd_bar:.4f}",
                trend["desc"],
                vol["desc"],
            ]

            above_zero = dif > 0 and dea > 0
            if above_zero:
                reasons.append("MACD在零轴上方（强势区）")

            bar_turning_red = macd_bar > 0
            if bar_turning_red:
                reasons.append("MACD柱由绿转红")

            has_divergence = divergence == "bullish_divergence"
            if has_divergence:
                reasons.append("MACD底背离（价格新低但DIF未新低，反转信号）")

            conditions = [
                dif > dea,                              # 金叉
                above_zero,                             # 零轴上方
                bar_turning_red,                        # 柱转红
                vol["is_amplified"],                    # 量能确认
                trend["direction"] == "up",             # 趋势向上
                has_divergence,                         # 底背离
            ]
            strength = self._calculate_strength(conditions)

            sl, tp, sl_desc = _compute_atr_stops(df, 'BUY', sl_mult=2.0, tp_mult=3.0)
            reasons.append(sl_desc)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=sl,
                take_profit=tp,
                position_size="40%"
            )
            signals.append(signal)

        # DIF下穿DEA（死叉）
        elif prev_dif >= prev_dea and dif < dea:
            reasons = [
                "MACD死叉（DIF下穿DEA）",
                f"当前价格: ¥{latest['close']:.2f}",
                trend["desc"],
                vol["desc"],
            ]

            has_divergence = divergence == "bearish_divergence"
            if has_divergence:
                reasons.append("MACD顶背离（价格新高但DIF未新高，见顶信号）")

            conditions = [
                dif < dea,                              # 死叉
                macd_bar < 0,                           # 柱为负
                trend["direction"] == "down",           # 趋势向下
                has_divergence,                         # 顶背离
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
    """KDJ策略（增强版：+量能确认 +趋势方向 +ATR止损）"""

    def __init__(self):
        super().__init__(name="KDJ")

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
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

        trend = _check_trend_alignment(df)
        vol = _get_volume_info(df)

        # K线上穿D线且在超卖区
        if prev_k <= prev_d and k > d and k < 30:
            reasons = [
                "KDJ金叉（K线上穿D线）",
                f"当前位置: K={k:.2f}, D={d:.2f}, J={j:.2f}",
                "处于超卖区（K<30）",
                trend["desc"],
                vol["desc"],
            ]

            conditions = [
                k > d,                                  # 金叉
                k < 30,                                 # 超卖区
                j < 20,                                 # J 值极低
                not vol["is_shrunk"],                   # 不缩量
                trend["direction"] != "down",           # 趋势不空
                trend["price_above_ma20"],              # 价格在 MA20 上方
            ]
            strength = self._calculate_strength(conditions)

            sl, tp, sl_desc = _compute_atr_stops(df, 'BUY', sl_mult=1.5, tp_mult=2.5)
            reasons.append(sl_desc)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=sl,
                take_profit=tp,
                position_size="30%"
            )
            signals.append(signal)

        # K线下穿D线且在超买区
        elif prev_k >= prev_d and k < d and k > 70:
            reasons = [
                "KDJ死叉（K线下穿D线）",
                f"当前位置: K={k:.2f}, D={d:.2f}, J={j:.2f}",
                "处于超买区（K>70）",
                trend["desc"],
                vol["desc"],
            ]

            conditions = [
                k < d,                                  # 死叉
                k > 70,                                 # 超买区
                j > 80,                                 # J 值极高
                trend["direction"] != "up",             # 趋势不多
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
    """RSI策略（增强版：+量能确认 +趋势方向 +ATR止损）"""

    def __init__(self, oversold: int = 30, overbought: int = 70):
        super().__init__(
            name="RSI",
            params={'oversold': oversold, 'overbought': overbought}
        )
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> List[Signal]:
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

        trend = _check_trend_alignment(df)
        vol = _get_volume_info(df)

        # RSI从超卖区回升
        if prev_rsi < self.oversold and rsi > self.oversold:
            reasons = [
                f"RSI从超卖区回升（RSI={rsi:.2f}）",
                "市场可能超跌反弹",
                trend["desc"],
                vol["desc"],
            ]

            conditions = [
                rsi > self.oversold,                    # 回升
                rsi < 50,                               # 还有上行空间
                vol["is_amplified"],                    # 量能确认
                trend["direction"] != "down",           # 趋势不空
                trend["price_above_ma20"],              # 价格在 MA20 上方
            ]
            strength = self._calculate_strength(conditions)

            sl, tp, sl_desc = _compute_atr_stops(df, 'BUY', sl_mult=1.5, tp_mult=2.5)
            reasons.append(sl_desc)

            signal = Signal(
                symbol=symbol,
                date=pd.to_datetime(latest['date']).date(),
                signal_type='BUY',
                strength=strength,
                price=latest['close'],
                strategy=self.name,
                reasons=reasons,
                entry_price=latest['close'],
                stop_loss=sl,
                take_profit=tp,
                position_size="35%"
            )
            signals.append(signal)

        # RSI从超买区回落
        elif prev_rsi > self.overbought and rsi < self.overbought:
            reasons = [
                f"RSI从超买区回落（RSI={rsi:.2f}）",
                "市场可能过热回调",
                trend["desc"],
                vol["desc"],
            ]

            conditions = [
                rsi < self.overbought,                  # 回落
                rsi > 50,                               # 还在高位
                trend["direction"] != "up",             # 趋势不多
                vol["is_amplified"],                    # 放量下跌
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
