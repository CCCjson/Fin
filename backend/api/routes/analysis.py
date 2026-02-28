"""
分析相关API
"""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
import pandas as pd

from api.models.schemas import (
    IndicatorRequest,
    IndicatorResponse,
    SignalRequest,
    SignalResponse,
    PatternRequest,
    PatternResponse
)
from data_engine import DataEngine
from analysis_engine import AnalysisEngine

router = APIRouter(prefix="/analysis", tags=["分析"])

# 全局实例
data_engine = DataEngine()
analysis_engine = AnalysisEngine()


@router.post("/indicators", response_model=IndicatorResponse)
async def calculate_indicators(request: IndicatorRequest):
    """
    计算技术指标

    支持的指标：
    - MA: 移动平均线
    - EMA: 指数移动平均
    - MACD: MACD指标
    - RSI: 相对强弱指标
    - KDJ: KDJ指标
    - BOLL: 布林带
    - ATR: 平均真实波幅
    - CCI: 顺势指标
    - 等等...
    """
    try:
        # 获取数据
        df = data_engine.get_daily_data(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date
        )

        if df.empty:
            raise HTTPException(status_code=404, detail=f"未找到 {request.symbol} 的数据")

        # 计算指标
        result_indicators = {}

        for indicator in request.indicators:
            indicator_upper = indicator.upper()
            params = request.params.get(indicator_upper, {})

            if indicator_upper == "MA":
                period = params.get("period", 20)
                df = analysis_engine.trend_indicators.sma(df, periods=[period])
                result_indicators[f"ma{period}"] = df[f"ma{period}"].tolist()

            elif indicator_upper == "EMA":
                period = params.get("period", 20)
                df = analysis_engine.trend_indicators.ema(df, periods=[period])
                result_indicators[f"ema{period}"] = df[f"ema{period}"].tolist()

            elif indicator_upper == "MACD":
                df = analysis_engine.trend_indicators.macd(df)
                if "macd_dif" in df.columns:
                    result_indicators["macd_dif"] = df["macd_dif"].tolist()
                    result_indicators["macd_dea"] = df["macd_dea"].tolist()
                    result_indicators["macd"] = df["macd"].tolist()
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="数据不足，无法计算MACD指标（需要至少26天数据）"
                    )

            elif indicator_upper == "RSI":
                period = params.get("period", 14)
                df = analysis_engine.momentum_indicators.rsi(df, period=period)
                if "rsi" in df.columns:
                    result_indicators["rsi"] = df["rsi"].tolist()
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"数据不足，无法计算RSI指标（需要至少{period+1}天数据）"
                    )

            elif indicator_upper == "KDJ":
                df = analysis_engine.momentum_indicators.kdj(df)
                result_indicators["kdj_k"] = df["kdj_k"].tolist()
                result_indicators["kdj_d"] = df["kdj_d"].tolist()
                result_indicators["kdj_j"] = df["kdj_j"].tolist()

            elif indicator_upper == "BOLL":
                df = analysis_engine.trend_indicators.boll(df)
                result_indicators["boll_upper"] = df["boll_upper"].tolist()
                result_indicators["boll_mid"] = df["boll_mid"].tolist()
                result_indicators["boll_lower"] = df["boll_lower"].tolist()

            elif indicator_upper == "ATR":
                period = params.get("period", 14)
                df = analysis_engine.volatility_indicators.atr(df, period=period)
                if "atr" in df.columns:
                    result_indicators["atr"] = df["atr"].tolist()
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"数据不足，无法计算ATR指标（需要至少{period+1}天数据）"
                    )

            elif indicator_upper == "CCI":
                period = params.get("period", 20)
                df = analysis_engine.momentum_indicators.cci(df, period=period)
                if "cci" in df.columns:
                    result_indicators["cci"] = df["cci"].tolist()
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"数据不足，无法计算CCI指标（需要至少{period+1}天数据）"
                    )

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的指标: {indicator}"
                )

        return IndicatorResponse(
            symbol=request.symbol,
            indicators=result_indicators,
            count=len(df)
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/signals", response_model=SignalResponse)
async def detect_signals(request: SignalRequest):
    """
    检测交易信号

    支持的信号类型：
    - MACD_CROSS: MACD金叉死叉
    - MA_CROSS: 均线交叉
    - RSI_OVERSOLD: RSI超卖
    - RSI_OVERBOUGHT: RSI超买
    - KDJ_CROSS: KDJ交叉
    - BOLL_BREAKOUT: 布林带突破
    """
    try:
        # 获取数据
        df = data_engine.get_daily_data(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date
        )

        if df.empty:
            raise HTTPException(status_code=404, detail=f"未找到 {request.symbol} 的数据")

        # 先计算技术指标（信号检测依赖指标列）
        df = analysis_engine.add_indicators(df)

        # 检测信号
        signals = analysis_engine.detect_signals(
            symbol=request.symbol,
            df=df
        )

        # 转换为JSON格式
        signals_list = []
        for signal in signals:
            signals_list.append({
                "date": str(signal.timestamp),
                "signal_type": signal.signal_type.value,
                "direction": signal.signal_type.value.upper(),
                "strength": signal.strength,
                "price": signal.price,
                "reason": signal.reason,
                "indicators": signal.indicators
            })

        return SignalResponse(
            symbol=request.symbol,
            signals=signals_list,
            count=len(signals_list)
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/patterns", response_model=PatternResponse)
async def detect_patterns(request: PatternRequest):
    """
    识别K线形态

    支持的形态：
    - DOJI: 十字星
    - HAMMER: 锤子线
    - SHOOTING_STAR: 射击之星
    - ENGULFING: 吞没形态
    - MORNING_STAR: 早晨之星
    - EVENING_STAR: 黄昏之星
    - 等等...
    """
    try:
        # 获取数据
        df = data_engine.get_daily_data(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date
        )

        if df.empty:
            raise HTTPException(status_code=404, detail=f"未找到 {request.symbol} 的数据")

        # 识别形态（返回 Dict[str, List[int]]，value 是出现位置的行索引）
        patterns = analysis_engine.detect_patterns(df)

        # 转换为JSON格式：用位置索引从 df 中取出对应行的 OHLCV 数据
        patterns_dict = {}
        for pattern_name, indices in patterns.items():
            if not indices:
                continue
            pattern_list = []
            for idx in indices:
                row = df.iloc[idx]
                pattern_list.append({
                    "date": str(df.index[idx]),
                    "pattern": pattern_name,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]) if "volume" in row else 0
                })
            patterns_dict[pattern_name] = pattern_list

        total_count = sum(len(v) for v in patterns_dict.values())

        return PatternResponse(
            symbol=request.symbol,
            patterns=patterns_dict,
            count=total_count
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/indicators/list")
async def list_indicators():
    """
    获取支持的技术指标列表
    """
    return {
        "趋势指标": ["MA", "EMA", "MACD", "BOLL", "DMI", "ADX"],
        "动量指标": ["RSI", "KDJ", "CCI", "ROC", "WR"],
        "波动率指标": ["ATR", "KC", "DC"],
        "成交量指标": ["OBV", "VWAP", "MFI", "VR"]
    }


@router.get("/signals/list")
async def list_signals():
    """
    获取支持的信号类型列表
    """
    return {
        "交叉信号": ["MACD_CROSS", "MA_CROSS", "KDJ_CROSS"],
        "超买超卖": ["RSI_OVERSOLD", "RSI_OVERBOUGHT", "KDJ_OVERSOLD", "KDJ_OVERBOUGHT"],
        "突破信号": ["BOLL_BREAKOUT", "PRICE_BREAKOUT"]
    }
