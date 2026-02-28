"""
信号检测器（增强版）

增强点：
- MACD强度用ATR归一化（消除股价影响）
- ADX<20时趋势型信号（MACD/MA交叉）强度打折
- 买卖信号增加量能确认（放量/缩量调整强度）
- MACD背离独立检测（波峰波谷算法）
- MACD柱缩量检测（红柱/绿柱连续缩短）
- K线形态整合进信号系统
- CCI/MFI辅助确认微调强度
"""
import pandas as pd
import numpy as np
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

        if df.empty or len(df) < 2:
            return signals

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # 提取ADX（用于趋势型信号过滤）
        adx = latest.get("adx", None)
        is_ranging = adx is not None and not pd.isna(adx) and adx < 20  # 震荡市

        # 提取量比（用于量能确认）
        volume_ratio = latest.get("volume_ratio", None)
        if volume_ratio is not None and pd.isna(volume_ratio):
            volume_ratio = None

        # 1. MACD 信号
        macd_signal = self._detect_macd_signal(latest, prev)
        if macd_signal:
            macd_signal = self._apply_volume_adjustment(macd_signal, volume_ratio)
            if is_ranging:
                macd_signal.strength *= 0.6  # 震荡市打折
            signals.append(macd_signal)

        # 2. KDJ 信号
        kdj_signal = self._detect_kdj_signal(latest, prev)
        if kdj_signal:
            signals.append(kdj_signal)

        # 3. RSI 信号
        rsi_signal = self._detect_rsi_signal(latest, prev)
        if rsi_signal:
            signals.append(rsi_signal)

        # 3b. RSI 持续超买/超卖预警
        rsi_sustained = self._detect_rsi_sustained(df)
        if rsi_sustained:
            signals.append(rsi_sustained)

        # 4. 布林带信号（含突破预警）
        boll_signals = self._detect_boll_signal(latest, prev)
        if boll_signals:
            if isinstance(boll_signals, list):
                signals.extend(boll_signals)
            else:
                signals.append(boll_signals)

        # 4b. 均线偏离度预警
        ma_dev_signal = self._detect_ma_deviation(latest)
        if ma_dev_signal:
            signals.append(ma_dev_signal)

        # 5. 均线信号
        ma_signal = self._detect_ma_signal(latest, prev)
        if ma_signal:
            ma_signal = self._apply_volume_adjustment(ma_signal, volume_ratio)
            if is_ranging:
                ma_signal.strength *= 0.6  # 震荡市打折
            signals.append(ma_signal)

        # 6. 金叉死叉信号
        cross_signal = self._detect_cross_signal(latest, prev, df)
        if cross_signal:
            cross_signal = self._apply_volume_adjustment(cross_signal, volume_ratio)
            if is_ranging:
                cross_signal.strength *= 0.6
            signals.append(cross_signal)

        # 7. MACD 背离信号（独立）
        div_signal = self._detect_macd_divergence(df)
        if div_signal:
            signals.append(div_signal)

        # 8. MACD 柱缩量信号
        hist_signal = self._detect_histogram_trend(df)
        if hist_signal:
            signals.append(hist_signal)

        # 9. K线形态信号
        pattern_signal = self._detect_pattern_signal(df)
        if pattern_signal:
            signals.append(pattern_signal)

        # 10. CCI/MFI 辅助确认（微调已有信号强度）
        signals = self._apply_cci_mfi_adjustment(signals, latest)

        # 11. 多指标共振超买/超卖预警
        multi_signal = self._detect_multi_overbought(latest)
        if multi_signal:
            signals.append(multi_signal)

        # 确保所有信号强度在 [0, 1]
        for s in signals:
            s.strength = max(0.0, min(s.strength, 1.0))

        return signals

    @staticmethod
    def _apply_volume_adjustment(signal: BaseSignal, volume_ratio: Optional[float]) -> BaseSignal:
        """量能确认：放量/缩量调整信号强度"""
        if volume_ratio is None:
            return signal

        if signal.signal_type == SignalType.BUY:
            if volume_ratio >= 1.5:
                signal.strength *= 1.2   # 放量突破，强度+20%
            elif volume_ratio < 0.8:
                signal.strength *= 0.7   # 缩量金叉，强度-30%
        elif signal.signal_type == SignalType.SELL:
            if volume_ratio >= 1.5:
                signal.strength *= 1.2   # 放量下跌，强度+20%
            elif volume_ratio < 0.8:
                signal.strength *= 0.8   # 缩量下跌，强度-20%

        return signal

    @staticmethod
    def _apply_cci_mfi_adjustment(signals: List[BaseSignal], latest: pd.Series) -> List[BaseSignal]:
        """CCI/MFI辅助确认：微调信号强度"""
        cci = latest.get("cci", None)
        mfi = latest.get("mfi", None)

        if cci is not None and pd.isna(cci):
            cci = None
        if mfi is not None and pd.isna(mfi):
            mfi = None

        for signal in signals:
            if signal.signal_type == SignalType.BUY:
                if cci is not None and cci < -100:
                    signal.strength *= 1.1  # CCI超卖确认买入
                if mfi is not None and mfi < 20:
                    signal.strength *= 1.1  # MFI超卖确认买入
            elif signal.signal_type == SignalType.SELL:
                if cci is not None and cci > 100:
                    signal.strength *= 1.1  # CCI超买确认卖出
                if mfi is not None and mfi > 80:
                    signal.strength *= 1.1  # MFI超买确认卖出

        return signals

    def _detect_macd_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测 MACD 信号（ATR归一化强度）"""
        if "macd_dif" not in latest or "macd_dea" not in latest:
            return None

        dif = latest["macd_dif"]
        dea = latest["macd_dea"]
        prev_dif = prev["macd_dif"]
        prev_dea = prev["macd_dea"]
        macd_bar = latest.get("macd", dif - dea)

        if pd.isna(dif) or pd.isna(dea) or pd.isna(prev_dif) or pd.isna(prev_dea):
            return None

        # ATR归一化强度计算
        def _calc_strength() -> float:
            atr = latest.get("atr", None)
            if atr and not pd.isna(atr) and atr > 0:
                return min(abs(dif - dea) / atr, 1.0)
            return min(abs(dif - dea) / latest["close"] * 100, 1.0)

        # 金叉：DIF 上穿 DEA
        if prev_dif <= prev_dea and dif > dea:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=_calc_strength(),
                reason="MACD 金叉（DIF上穿DEA）",
                indicators={"macd_dif": dif, "macd_dea": dea, "macd": macd_bar}
            )

        # 死叉：DIF 下穿 DEA
        if prev_dif >= prev_dea and dif < dea:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=_calc_strength(),
                reason="MACD 死叉（DIF下穿DEA）",
                indicators={"macd_dif": dif, "macd_dea": dea, "macd": macd_bar}
            )

        return None

    def _detect_kdj_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测 KDJ 信号（D线作为强度调节器，不做硬门槛）"""
        if "kdj_k" not in latest or "kdj_d" not in latest or "kdj_j" not in latest:
            return None

        k = latest["kdj_k"]
        d = latest["kdj_d"]
        j = latest["kdj_j"]

        if pd.isna(k) or pd.isna(d):
            return None

        # 超卖区金叉：K<20 且 K上穿D，D值决定强度
        if k < 20 and prev["kdj_k"] <= prev["kdj_d"] and k > d:
            if d < 20:
                strength = 0.8    # 双极端，最强
            elif d < 35:
                strength = 0.6   # D也偏低，确认性好
            else:
                strength = 0.4    # 只有K冲低，可能是假信号
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=strength,
                reason=f"KDJ 超卖区金叉（K={k:.1f}, D={d:.1f}）",
                indicators={"kdj_k": k, "kdj_d": d, "kdj_j": j}
            )

        # 超买区死叉：K>80 且 K下穿D，D值决定强度
        if k > 80 and prev["kdj_k"] >= prev["kdj_d"] and k < d:
            if d > 80:
                strength = 0.8   # 双极端，最强
            elif d > 65:
                strength = 0.6   # D也偏高，确认性好
            else:
                strength = 0.4   # 只有K冲高，可能是假信号
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=strength,
                reason=f"KDJ 超买区死叉（K={k:.1f}, D={d:.1f}）",
                indicators={"kdj_k": k, "kdj_d": d, "kdj_j": j}
            )

        return None

    def _detect_rsi_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测 RSI 信号"""
        if "rsi" not in latest:
            return None

        rsi = latest["rsi"]
        prev_rsi = prev["rsi"]

        if pd.isna(rsi) or pd.isna(prev_rsi):
            return None

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

    def _detect_rsi_sustained(self, df: pd.DataFrame) -> Optional[BaseSignal]:
        """
        RSI 持续超买/超卖预警

        RSI连续3天>70 → SELL预警；>80 加强
        RSI连续3天<30 → BUY预警；<20 加强
        """
        if len(df) < 3 or "rsi" not in df.columns:
            return None

        recent_rsi = df["rsi"].iloc[-3:].values
        if np.any(np.isnan(recent_rsi)):
            return None

        latest = df.iloc[-1]
        current_rsi = recent_rsi[-1]

        # 持续超买
        if all(r > 70 for r in recent_rsi):
            strength = 0.6 if current_rsi > 80 else 0.4
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=strength,
                reason=f"RSI 持续超买预警（连续3天>70，当前RSI={current_rsi:.1f}）",
                indicators={"rsi": current_rsi, "rsi_3d": recent_rsi.tolist()}
            )

        # 持续超卖
        if all(r < 30 for r in recent_rsi):
            strength = 0.6 if current_rsi < 20 else 0.4
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=latest["close"],
                strength=strength,
                reason=f"RSI 持续超卖预警（连续3天<30，当前RSI={current_rsi:.1f}）",
                indicators={"rsi": current_rsi, "rsi_3d": recent_rsi.tolist()}
            )

        return None

    def _detect_boll_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[List[BaseSignal]]:
        """检测布林带信号（含突破预警 + 回落确认）"""
        if "boll_upper" not in latest or "boll_lower" not in latest:
            return None

        close = latest["close"]
        prev_close = prev["close"]
        upper = latest["boll_upper"]
        lower = latest["boll_lower"]
        prev_lower = prev["boll_lower"]
        prev_upper = prev["boll_upper"]

        if pd.isna(upper) or pd.isna(lower):
            return None

        boll_indicators = {
            "boll_upper": upper,
            "boll_mid": latest.get("boll_mid"),
            "boll_lower": lower
        }

        # --- 突破预警（新增）---
        # 价格从下方突破上轨 → 短期超买预警
        if prev_close <= prev_upper and close > upper:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.4,
                reason=f"价格突破布林上轨预警（{close:.2f} > 上轨{upper:.2f}）",
                indicators=boll_indicators
            )

        # 价格从上方跌破下轨 → 短期超卖预警
        if prev_close >= prev_lower and close < lower:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.4,
                reason=f"价格跌破布林下轨预警（{close:.2f} < 下轨{lower:.2f}）",
                indicators=boll_indicators
            )

        # --- 回落/反弹确认（原有逻辑）---
        # 价格从下轨下方回到下轨上方 → 超卖反弹确认
        if prev_close < prev_lower and close >= lower:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.6,
                reason="布林下轨反弹确认",
                indicators=boll_indicators
            )

        # 价格从上轨上方回到上轨下方 → 超买回落确认
        if prev_close > prev_upper and close <= upper:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.6,
                reason="布林上轨回落确认",
                indicators=boll_indicators
            )

        return None

    def _detect_ma_deviation(self, latest: pd.Series) -> Optional[BaseSignal]:
        """
        均线偏离度预警

        价格偏离MA20超过10% → 预警
        价格偏离MA20超过15% → 加强预警
        """
        if "ma20" not in latest:
            return None

        ma20 = latest["ma20"]
        close = latest["close"]

        if pd.isna(ma20) or ma20 == 0:
            return None

        deviation = (close - ma20) / ma20 * 100

        if deviation > 15:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.6,
                reason=f"价格极端偏离MA20（+{deviation:.1f}%，均值回归风险高）",
                indicators={"ma20": ma20, "deviation_pct": round(deviation, 2)}
            )
        elif deviation > 10:
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.4,
                reason=f"价格严重偏离MA20（+{deviation:.1f}%，注意回调风险）",
                indicators={"ma20": ma20, "deviation_pct": round(deviation, 2)}
            )
        elif deviation < -15:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.6,
                reason=f"价格极端偏离MA20（{deviation:.1f}%，超跌反弹机会）",
                indicators={"ma20": ma20, "deviation_pct": round(deviation, 2)}
            )
        elif deviation < -10:
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=0.4,
                reason=f"价格严重偏离MA20（{deviation:.1f}%，关注反弹机会）",
                indicators={"ma20": ma20, "deviation_pct": round(deviation, 2)}
            )

        return None

    def _detect_ma_signal(self, latest: pd.Series, prev: pd.Series) -> Optional[BaseSignal]:
        """检测均线信号"""
        if "ma5" not in latest or "ma10" not in latest or "ma20" not in latest:
            return None

        ma5 = latest["ma5"]
        ma10 = latest["ma10"]
        ma20 = latest["ma20"]

        if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
            return None

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

        if pd.isna(latest["ma5"]) or pd.isna(latest["ma20"]):
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

    def _detect_macd_divergence(self, df: pd.DataFrame) -> Optional[BaseSignal]:
        """
        MACD背离检测（独立信号）— 波峰波谷算法

        底背离：找至少2个价格低点，第二个价格更低但DIF更高
        顶背离：找至少2个价格高点，第二个价格更高但DIF更低
        """
        lookback = 25
        if len(df) < lookback + 1 or "macd_dif" not in df.columns:
            return None

        recent = df.iloc[-lookback:]
        latest = df.iloc[-1]

        closes = recent["close"].values
        difs = recent["macd_dif"].values

        # 过滤NaN
        valid_mask = ~(np.isnan(difs) | np.isnan(closes))
        if valid_mask.sum() < 10:
            return None

        closes_v = closes[valid_mask]
        difs_v = difs[valid_mask]
        indices = np.where(valid_mask)[0]

        # 找局部低点（波谷）：比前后一根都低
        local_lows = []
        for i in range(1, len(closes_v) - 1):
            if closes_v[i] < closes_v[i - 1] and closes_v[i] < closes_v[i + 1]:
                local_lows.append(i)

        # 找局部高点（波峰）
        local_highs = []
        for i in range(1, len(closes_v) - 1):
            if closes_v[i] > closes_v[i - 1] and closes_v[i] > closes_v[i + 1]:
                local_highs.append(i)

        # 底背离检测：至少2个低点，后一个价格更低但DIF更高
        if len(local_lows) >= 2:
            last_low = local_lows[-1]
            prev_low = local_lows[-2]
            if (closes_v[last_low] < closes_v[prev_low] and
                    difs_v[last_low] > difs_v[prev_low]):
                # 确保最近的低点离当前不太远（5根K线内）
                if indices[last_low] >= len(recent) - 6:
                    return BaseSignal(
                        signal_type=SignalType.BUY,
                        symbol=self.symbol,
                        timestamp=latest.name,
                        price=latest["close"],
                        strength=0.5,
                        reason="MACD底背离预警（价格新低但DIF未新低）",
                        indicators={
                            "macd_dif": latest["macd_dif"],
                            "divergence_type": "bullish"
                        }
                    )

        # 顶背离检测：至少2个高点，后一个价格更高但DIF更低
        if len(local_highs) >= 2:
            last_high = local_highs[-1]
            prev_high = local_highs[-2]
            if (closes_v[last_high] > closes_v[prev_high] and
                    difs_v[last_high] < difs_v[prev_high]):
                if indices[last_high] >= len(recent) - 6:
                    return BaseSignal(
                        signal_type=SignalType.SELL,
                        symbol=self.symbol,
                        timestamp=latest.name,
                        price=latest["close"],
                        strength=0.5,
                        reason="MACD顶背离预警（价格新高但DIF未新高）",
                        indicators={
                            "macd_dif": latest["macd_dif"],
                            "divergence_type": "bearish"
                        }
                    )

        return None

    def _detect_histogram_trend(self, df: pd.DataFrame) -> Optional[BaseSignal]:
        """
        MACD柱缩量检测

        红柱连续缩短 → 多头动能衰减预警（SELL）
        绿柱连续缩短 → 空头动能衰减预警（BUY）
        """
        if len(df) < 5 or "macd" not in df.columns:
            return None

        bars = df["macd"].iloc[-5:].values

        # 过滤NaN
        if np.any(np.isnan(bars)):
            return None

        latest = df.iloc[-1]

        # 取最后4根柱子
        last4 = bars[-4:]

        # 红柱连续缩短（至少4根连续变短的正柱）
        if all(b > 0 for b in last4):
            if all(last4[i] > last4[i + 1] for i in range(3)):
                return BaseSignal(
                    signal_type=SignalType.SELL,
                    symbol=self.symbol,
                    timestamp=latest.name,
                    price=latest["close"],
                    strength=0.4,
                    reason="MACD红柱连续缩短（多头动能衰减）",
                    indicators={"macd_bars": last4.tolist()}
                )

        # 绿柱连续缩短（绝对值变小）
        if all(b < 0 for b in last4):
            abs_bars = [abs(b) for b in last4]
            if all(abs_bars[i] > abs_bars[i + 1] for i in range(3)):
                return BaseSignal(
                    signal_type=SignalType.BUY,
                    symbol=self.symbol,
                    timestamp=latest.name,
                    price=latest["close"],
                    strength=0.4,
                    reason="MACD绿柱连续缩短（空头动能衰减）",
                    indicators={"macd_bars": last4.tolist()}
                )

        return None

    def _detect_pattern_signal(self, df: pd.DataFrame) -> Optional[BaseSignal]:
        """
        K线形态整合进信号系统

        检查最新K线是否命中看涨/看跌形态
        """
        try:
            from analysis_engine.patterns.candlestick import CandlestickPatterns
        except ImportError:
            return None

        if len(df) < 5:
            return None

        patterns = CandlestickPatterns.detect_all(df)
        latest = df.iloc[-1]
        last_idx = len(df) - 1

        # 看涨形态
        bullish_patterns = ["hammer", "engulfing_bullish", "morning_star",
                           "three_white_soldiers", "harami_bullish"]
        # 看跌形态
        bearish_patterns = ["shooting_star", "engulfing_bearish", "evening_star",
                           "three_black_crows", "harami_bearish"]

        # 检查最新一根K线是否命中
        for p_name in bullish_patterns:
            if p_name in patterns and last_idx in patterns[p_name]:
                return BaseSignal(
                    signal_type=SignalType.BUY,
                    symbol=self.symbol,
                    timestamp=latest.name,
                    price=latest["close"],
                    strength=0.5,
                    reason=f"看涨K线形态: {p_name}",
                    indicators={"pattern": p_name, "pattern_type": "bullish"}
                )

        for p_name in bearish_patterns:
            if p_name in patterns and last_idx in patterns[p_name]:
                return BaseSignal(
                    signal_type=SignalType.SELL,
                    symbol=self.symbol,
                    timestamp=latest.name,
                    price=latest["close"],
                    strength=0.5,
                    reason=f"看跌K线形态: {p_name}",
                    indicators={"pattern": p_name, "pattern_type": "bearish"}
                )

        return None

    def _detect_multi_overbought(self, latest: pd.Series) -> Optional[BaseSignal]:
        """
        多指标共振超买/超卖预警

        统计多个超买/超卖因子，>=3个同时满足时发出预警。
        与单项预警维度不同：单项说"哪里有问题"，共振说"问题有多严重"。
        """
        # --- 超买因子统计 ---
        overbought_factors = []
        rsi = latest.get("rsi", None)
        if rsi is not None and not pd.isna(rsi) and rsi > 70:
            overbought_factors.append(f"RSI={rsi:.1f}>70")

        k = latest.get("kdj_k", None)
        if k is not None and not pd.isna(k) and k > 80:
            overbought_factors.append(f"KDJ_K={k:.1f}>80")

        close = latest["close"]
        upper = latest.get("boll_upper", None)
        if upper is not None and not pd.isna(upper) and close > upper:
            overbought_factors.append(f"价格{close:.2f}>布林上轨{upper:.2f}")

        ma20 = latest.get("ma20", None)
        if ma20 is not None and not pd.isna(ma20) and ma20 > 0:
            dev = (close - ma20) / ma20 * 100
            if dev > 10:
                overbought_factors.append(f"偏离MA20 +{dev:.1f}%")

        if len(overbought_factors) >= 3:
            strength = 0.3 + 0.1 * len(overbought_factors)
            return BaseSignal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=min(strength, 1.0),
                reason=f"多指标共振超买预警（{len(overbought_factors)}项：{', '.join(overbought_factors)}）",
                indicators={"overbought_count": len(overbought_factors)}
            )

        # --- 超卖因子统计 ---
        oversold_factors = []
        if rsi is not None and not pd.isna(rsi) and rsi < 30:
            oversold_factors.append(f"RSI={rsi:.1f}<30")

        if k is not None and not pd.isna(k) and k < 20:
            oversold_factors.append(f"KDJ_K={k:.1f}<20")

        lower = latest.get("boll_lower", None)
        if lower is not None and not pd.isna(lower) and close < lower:
            oversold_factors.append(f"价格{close:.2f}<布林下轨{lower:.2f}")

        if ma20 is not None and not pd.isna(ma20) and ma20 > 0:
            dev = (close - ma20) / ma20 * 100
            if dev < -10:
                oversold_factors.append(f"偏离MA20 {dev:.1f}%")

        if len(oversold_factors) >= 3:
            strength = 0.3 + 0.1 * len(oversold_factors)
            return BaseSignal(
                signal_type=SignalType.BUY,
                symbol=self.symbol,
                timestamp=latest.name,
                price=close,
                strength=min(strength, 1.0),
                reason=f"多指标共振超卖预警（{len(oversold_factors)}项：{', '.join(oversold_factors)}）",
                indicators={"oversold_count": len(oversold_factors)}
            )

        return None
