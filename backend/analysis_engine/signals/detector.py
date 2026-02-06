"""
信号检测器
"""
import pandas as pd
from typing import List, Optional
from datetime import datetime
from loguru import logger

from .base import BaseSignal, SignalType


class SignalDetector:
    """信号检测器 - 基于技术指标检测交易信号"""

    def __init__(self, symbol: str):
        self.symbol = symbol

    def detect_all(self, df: pd.DataFrame) -> List[BaseSignal]:
        """
        检测所有信号

        Args:
            df: 包含技术指标的 DataFrame

        Returns:
            信号列表
        """
        signals = []

        # 获取最新一行数据
        if df.empty or len(df) < 2:
            return signals

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # 1. MACD 信号
        macd_signal = self._detect_macd_signal(latest, prev)
        if macd_signal:
            signals.append(macd_signal)

        # 2. KDJ 信号
        kdj_signal = self._detect_kdj_signal(latest, prev)
        if kdj_signal:
            signals.append(kdj_signal)

        # 3. RSI 信号
        rsi_signal = self._detect_rsi_signal(latest, prev)
        if rsi_signal:
            signals.append(rsi_signal)

        # 4. 布林带信号
        boll_signal = self._detect_boll_signal(latest, prev)
        if boll_signal:
            signals.append(boll_signal)

        # 5. 均线信号
        ma_signal = self._detect_ma_signal(latest, prev)
        if ma_signal:
            signals.append(ma_signal)

        # 6. 金叉死叉信号
        cross_signal = self._detect_cross_signal(latest, prev, df)
        if cross_signal:
            signals.append(cross_signal)

        return signals

    def _detect_macd_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测 MACD 信号"""
        if "macd" not in latest or "macd_signal" not in latest:
            return None

        macd = latest["macd"]
        macd_signal = latest["macd_signal"]
        prev_macd = prev["macd"]
        prev_signal = prev["macd_signal"]

        # 金叉：MACD 上穿信号线
        if prev_macd <= prev_signal and macd > macd_signal:
            strength = min(abs(macd - macd_signal) / latest["close"] * 100, 1.0)
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=strength,
                reason="MACD 金叉",
                indicators={
                    "macd": macd,
                    "macd_signal": macd_signal,
                    "macd_hist": latest["macd_hist"]
                }
            )

        # 死叉：MACD 下穿信号线
        if prev_macd >= prev_signal and macd < macd_signal:
            strength = min(abs(macd - macd_signal) / latest["close"] * 100, 1.0)
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=strength,
                reason="MACD 死叉",
                indicators={
                    "macd": macd,
                    "macd_signal": macd_signal,
                    "macd_hist": latest["macd_hist"]
                }
            )

        return None

    def _detect_kdj_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测 KDJ 信号"""
        if "kdj_k" not in latest or "kdj_d" not in latest or "kdj_j" not in latest:
            return None

        k = latest["kdj_k"]
        d = latest["kdj_d"]
        j = latest["kdj_j"]

        # 超卖区金叉（K、D 都在 20 以下，K 上穿 D）
        if k < 20 and d < 20 and prev["kdj_k"] <= prev["kdj_d"] and k > d:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=0.8,
                reason="KDJ 超卖区金叉",
                indicators={"kdj_k": k, "kdj_d": d, "kdj_j": j}
            )

        # 超买区死叉（K、D 都在 80 以上，K 下穿 D）
        if k > 80 and d > 80 and prev["kdj_k"] >= prev["kdj_d"] and k < d:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=0.8,
                reason="KDJ 超买区死叉",
                indicators={"kdj_k": k, "kdj_d": d, "kdj_j": j}
            )

        return None

    def _detect_rsi_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测 RSI 信号"""
        if "rsi" not in latest:
            return None

        rsi = latest["rsi"]
        prev_rsi = prev["rsi"]

        # 超卖反转（RSI 从 30 以下反弹）
        if prev_rsi < 30 and rsi >= 30:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=0.7,
                reason="RSI 超卖反转",
                indicators={"rsi": rsi}
            )

        # 超买回落（RSI 从 70 以上回落）
        if prev_rsi > 70 and rsi <= 70:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=0.7,
                reason="RSI 超买回落",
                indicators={"rsi": rsi}
            )

        return None

    def _detect_boll_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测布林带信号"""
        if "boll_upper" not in latest or "boll_lower" not in latest:
            return None

        close = latest["close"]
        prev_close = prev["close"]
        upper = latest["boll_upper"]
        lower = latest["boll_lower"]
        prev_lower = prev["boll_lower"]
        prev_upper = prev["boll_upper"]

        # 突破下轨（价格从下方突破下轨）
        if prev_close < prev_lower and close >= lower:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.6,
                reason="突破布林下轨",
                indicators={
                    "boll_upper": upper,
                    "boll_mid": latest["boll_mid"],
                    "boll_lower": lower
                }
            )

        # 突破上轨（价格从上方突破上轨）
        if prev_close > prev_upper and close <= upper:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.6,
                reason="突破布林上轨",
                indicators={
                    "boll_upper": upper,
                    "boll_mid": latest["boll_mid"],
                    "boll_lower": lower
                }
            )

        return None

    def _detect_ma_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测均线信号"""
        if "ma5" not in latest or "ma10" not in latest or "ma20" not in latest:
            return None

        ma5 = latest["ma5"]
        ma10 = latest["ma10"]
        ma20 = latest["ma20"]

        # 多头排列（MA5 > MA10 > MA20）
        if ma5 > ma10 > ma20:
            if not (prev["ma5"] > prev["ma10"] > prev["ma20"]):
                return BaseSignal(
                    signal_type=SignalType.BUY,
                    symbol=self.symbol,
                    timestamp=latest.name,
                    price=latest["close"],
                    strength=0.7,
                    reason="均线多头排列",
                    indicators={"ma5": ma5, "ma10": ma10, "ma20": ma20}
                )

        # 空头排列（MA5 < MA10 < MA20）
        if ma5 < ma10 < ma20:
            if not (prev["ma5"] < prev["ma10"] < prev["ma20"]):
                return BaseSignal(
                    signal_type=SignalType.SELL,
                    symbol=self.symbol,
                    timestamp=latest.name,
                    price=latest["close"],
                    strength=0.7,
                    reason="均线空头排列",
                    indicators={"ma5": ma5, "ma10": ma10, "ma20": ma20}
                )

        return None

    def _detect_cross_signal(
        self, latest: pd.Series, prev: pd.Series, df: pd.DataFrame
    ) -> Optional[BaseSignal]:
        """检测金叉死叉信号"""
        if "ma5" not in latest or "ma20" not in latest:
            return None

        # 短期均线金叉长期均线
        if prev["ma5"] <= prev["ma20"] and latest["ma5"] > latest["ma20"]:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=0.8,
                reason="均线金叉 (MA5 上穿 MA20)",
                indicators={"ma5": latest["ma5"], "ma20": latest["ma20"]}
            )

        # 短期均线死叉长期均线
        if prev["ma5"] >= prev["ma20"] and latest["ma5"] < latest["ma20"]:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=0.8,
                reason="均线死叉 (MA5 下穿 MA20)",
                indicators={"ma5": latest["ma5"], "ma20": latest["ma20"]}
            )

        return None
