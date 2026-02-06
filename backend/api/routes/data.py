"""
数据相关API
"""
from fastapi import APIRouter, HTTPException
from typing import List
import pandas as pd
from datetime import datetime

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
