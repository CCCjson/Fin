"""
信号生成API
"""
import asyncio
from datetime import datetime, date
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional
from loguru import logger
from sqlalchemy import func

from strategy.signal_generator import SignalGenerator
from data_engine.storage.database import get_session
from data_engine.storage.models import DailyQuote, Signal

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
    symbols: Optional[List[str]] = Field(None, description="股票代码列表，为空则扫描数据库中所有股票")
    lookback_days: int = Field(60, description="回溯天数", ge=30, le=365)
    save_to_db: bool = Field(True, description="是否保存到数据库")
    limit: Optional[int] = Field(None, description="最多扫描股票数量，为空则扫描全部")
    db_only: bool = Field(True, description="仅使用数据库数据，不联网拉取（扫描更快）")


@router.get("/data-freshness", summary="查询数据库数据新鲜度")
async def get_data_freshness():
    """
    查询数据库中行情数据的最新日期，并判断是否过期。

    返回:
    - latest_date: 数据库中最新的行情日期
    - is_stale: 数据是否已过期（最新日期 < 今天）
    """
    try:
        session = get_session()
        max_date_row = session.query(func.max(DailyQuote.date)).first()
        session.close()

        if max_date_row and max_date_row[0]:
            latest = max_date_row[0]
            if isinstance(latest, str):
                latest = datetime.strptime(latest, "%Y-%m-%d").date()
            elif isinstance(latest, datetime):
                latest = latest.date()
            latest_date_str = latest.isoformat()
            is_stale = latest < date.today()
        else:
            latest_date_str = None
            is_stale = True

        return {
            "latest_date": latest_date_str,
            "is_stale": is_stale
        }
    except Exception as e:
        logger.error(f"查询数据新鲜度失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/today-status", summary="查询今日信号是否已生成")
async def get_today_status():
    """
    查询今日是否已有信号记录，用于前端防止重复扫描。

    返回:
    - has_today_signals: 今日是否已有信号
    - signal_count: 今日信号数量
    - signal_date: 查询的日期
    """
    try:
        today = date.today()
        session = get_session()
        count = session.query(func.count(Signal.id)).filter(Signal.date == today).scalar() or 0
        session.close()

        return {
            "has_today_signals": count > 0,
            "signal_count": count,
            "signal_date": today.isoformat(),
        }
    except Exception as e:
        logger.error(f"查询今日信号状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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

    - **symbols**: 股票代码列表（可选，为空则扫描数据库中所有股票）
    - **lookback_days**: 回溯天数（30-365天）
    - **save_to_db**: 是否保存到数据库
    - **limit**: 最多扫描股票数量

    这个接口可以定时调用，用于每日市场扫描和信号生成
    """
    try:
        generator = SignalGenerator()
        results = generator.scan_market(
            symbols=request.symbols,
            lookback_days=request.lookback_days,
            save_to_db=request.save_to_db,
            limit=request.limit
        )

        return results

    except Exception as e:
        logger.error(f"市场扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan/stream", summary="流式扫描市场生成信号（带进度）")
async def scan_market_stream(request: MarketScanRequest):
    """
    流式扫描市场，通过 NDJSON 实时推送扫描进度。

    返回三种事件:
    - start: 扫描开始，包含总股票数
    - progress: 每只股票扫描后推送当前进度
    - complete: 扫描完成，包含汇总统计
    """
    try:
        generator = SignalGenerator()
        sync_gen = generator.scan_market_stream(
            symbols=request.symbols,
            lookback_days=request.lookback_days,
            save_to_db=request.save_to_db,
            limit=request.limit,
            db_only=request.db_only
        )

        async def _flushing_wrapper():
            """将 sync generator 包装为 async，每个 chunk 后让出事件循环以触发网络刷新"""
            for chunk in sync_gen:
                yield chunk
                await asyncio.sleep(0)

        return StreamingResponse(
            _flushing_wrapper(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
    except Exception as e:
        logger.error(f"流式市场扫描失败: {e}")
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
