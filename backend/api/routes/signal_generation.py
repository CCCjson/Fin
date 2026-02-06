"""
信号生成API
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from loguru import logger

from strategy.signal_generator import SignalGenerator

router = APIRouter(prefix="/signals", tags=["信号生成"])


class GenerateSignalRequest(BaseModel):
    """生成信号请求"""
    symbol: str = Field(..., description="股票代码", example="688576.SH")
    start_date: str = Field(..., description="开始日期", example="2025-01-01")
    end_date: str = Field(..., description="结束日期", example="2025-12-31")
    save_to_db: bool = Field(True, description="是否保存到数据库")


class BatchGenerateSignalRequest(BaseModel):
    """批量生成信号请求"""
    symbols: List[str] = Field(..., description="股票代码列表")
    start_date: str = Field(..., description="开始日期", example="2025-01-01")
    end_date: str = Field(..., description="结束日期", example="2025-12-31")
    save_to_db: bool = Field(True, description="是否保存到数据库")


class MarketScanRequest(BaseModel):
    """市场扫描请求"""
    symbols: Optional[List[str]] = Field(None, description="股票代码列表，为空则使用默认列表")
    lookback_days: int = Field(60, description="回溯天数", ge=30, le=365)
    save_to_db: bool = Field(True, description="是否保存到数据库")


@router.post("/generate", summary="生成单个股票的交易信号")
async def generate_signal(request: GenerateSignalRequest):
    """
    为单个股票生成交易信号

    - **symbol**: 股票代码
    - **start_date**: 开始日期
    - **end_date**: 结束日期
    - **save_to_db**: 是否保存到数据库
    """
    try:
        generator = SignalGenerator()
        signals = generator.generate_signals_for_symbol(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
            save_to_db=request.save_to_db
        )

        return {
            'symbol': request.symbol,
            'count': len(signals),
            'signals': [s.to_dict() for s in signals]
        }

    except Exception as e:
        logger.error(f"生成信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate/batch", summary="批量生成多个股票的交易信号")
async def batch_generate_signals(request: BatchGenerateSignalRequest):
    """
    为多个股票批量生成交易信号

    - **symbols**: 股票代码列表
    - **start_date**: 开始日期
    - **end_date**: 结束日期
    - **save_to_db**: 是否保存到数据库
    """
    try:
        generator = SignalGenerator()
        results = generator.generate_signals_for_symbols(
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            save_to_db=request.save_to_db
        )

        total_signals = sum(r['count'] for r in results.values())

        return {
            'total_signals': total_signals,
            'symbols_count': len(request.symbols),
            'results': results
        }

    except Exception as e:
        logger.error(f"批量生成信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan", summary="扫描市场生成最新信号")
async def scan_market(request: MarketScanRequest):
    """
    扫描市场并为股票池中的所有股票生成最新信号

    - **symbols**: 股票代码列表（可选，为空则使用默认股票池）
    - **lookback_days**: 回溯天数（30-365天）
    - **save_to_db**: 是否保存到数据库

    这个接口可以定时调用，用于每日市场扫描和信号生成
    """
    try:
        generator = SignalGenerator()
        results = generator.scan_market(
            symbols=request.symbols,
            lookback_days=request.lookback_days,
            save_to_db=request.save_to_db
        )

        return results

    except Exception as e:
        logger.error(f"市场扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategies", summary="获取可用策略列表")
async def get_strategies():
    """获取所有可用的交易策略"""
    strategies = [
        {
            'name': 'MA_CROSS',
            'description': '均线交叉策略',
            'params': {'fast_period': 5, 'slow_period': 20}
        },
        {
            'name': 'MACD',
            'description': 'MACD指标策略',
            'params': {'fast_period': 12, 'slow_period': 26, 'signal_period': 9}
        },
        {
            'name': 'KDJ',
            'description': 'KDJ随机指标策略',
            'params': {'period': 9, 'm1': 3, 'm2': 3}
        },
        {
            'name': 'RSI',
            'description': 'RSI相对强弱指标策略',
            'params': {'oversold': 30, 'overbought': 70}
        }
    ]

    return {
        'count': len(strategies),
        'strategies': strategies
    }
