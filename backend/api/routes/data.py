"""
数据相关API
"""
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import List
import pandas as pd
from datetime import datetime
from loguru import logger

from api.models.schemas import (
    StockDataRequest,
    StockDataResponse,
    StockListResponse,
    MessageResponse
)
from data_engine import DataEngine

router = APIRouter(prefix="/data", tags=["数据"])

# 全局数据引擎实例
data_engine = DataEngine()


@router.post("/daily", response_model=StockDataResponse)
async def get_daily_data(request: StockDataRequest):
    """
    获取日线数据
    """
    try:
        df = data_engine.get_daily_data(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date
        )

        if df.empty:
            raise HTTPException(status_code=404, detail=f"未找到 {request.symbol} 的数据")

        # 转换为JSON格式
        df_reset = df.reset_index()
        df_reset['date'] = df_reset['date'].astype(str)
        data = df_reset.to_dict('records')

        return StockDataResponse(
            symbol=request.symbol,
            data=data,
            count=len(data)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stocks", response_model=StockListResponse)
async def get_stock_list(market: str = "A"):
    """
    获取股票列表

    Args:
        market: 市场类型 (A=A股, HK=港股, US=美股)
    """
    try:
        stocks = data_engine.get_stock_list(market=market)

        return StockListResponse(
            stocks=stocks,
            count=len(stocks)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update", response_model=MessageResponse)
async def update_data(symbol: str, days: int = 30):
    """
    更新股票数据

    Args:
        symbol: 股票代码
        days: 更新天数
    """
    try:
        data_engine.update_stock_data(symbol=symbol, days=days)

        return MessageResponse(
            message=f"已更新 {symbol} 最近 {days} 天的数据",
            success=True
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info/{symbol}")
async def get_stock_info(symbol: str):
    """
    获取股票基本信息
    """
    try:
        info = data_engine.get_stock_info(symbol)

        if not info:
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的信息")

        return info

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 全市场增量更新 ====================

@router.post("/update-daily/stream", summary="流式增量更新全市场日线数据")
async def update_daily_stream():
    """
    增量更新全市场 A 股日线数据（流式进度）

    复用 EastMoneyCrawler + ProxyManager，自动代理切换。
    只拉每只股票 DB 中缺失的日期范围。
    """
    try:
        from data_engine.daily_updater import DailyUpdater

        updater = DailyUpdater()
        sync_gen = updater.update_stream()

        async def _flushing_wrapper():
            for chunk in sync_gen:
                yield chunk
                await asyncio.sleep(0)

        return StreamingResponse(
            _flushing_wrapper(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.error(f"全市场增量更新失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/update-status", summary="查询数据更新状态")
async def get_update_status():
    """查询全市场数据覆盖状态和上次更新信息"""
    try:
        from data_engine.daily_updater import DailyUpdater

        updater = DailyUpdater()
        return updater.get_update_status()
    except Exception as e:
        logger.error(f"查询更新状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
