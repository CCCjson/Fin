"""
数据相关API
"""
import asyncio
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
import pandas as pd
from datetime import datetime
from loguru import logger
from sqlalchemy import or_

from api.models.schemas import (
    StockDataRequest,
    StockDataResponse,
    StockListResponse,
    MessageResponse
)
from data_engine import DataEngine
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo, UserSettings

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


@router.get("/stocks/search", summary="搜索股票（自动补全）")
async def search_stocks(
    q: str = Query(..., min_length=1, description="搜索关键词（代码或名称）"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    模糊搜索股票，支持按代码或名称匹配。
    用于前端输入框的自动补全。
    """
    session = get_session()
    try:
        keyword = f"%{q}%"
        results = (
            session.query(StockInfo)
            .filter(
                StockInfo.is_active == True,
                or_(
                    StockInfo.symbol.like(keyword),
                    StockInfo.name.like(keyword),
                ),
            )
            .order_by(
                # 前缀匹配排在前面
                StockInfo.symbol.like(f"{q}%").desc(),
                StockInfo.symbol,
            )
            .limit(limit)
            .all()
        )

        return [
            {
                "symbol": s.symbol,
                "name": s.name,
                "market": s.market,
                "industry": s.industry,
            }
            for s in results
        ]
    finally:
        session.close()


# ==================== 用户设置 ====================

# 默认设置（启动时自动写入）
_DEFAULT_SETTINGS = {
    "total_capital": {"value": "200000", "description": "总资金（元），用于计算仓位占比"},
}


def _init_default_settings():
    """启动时初始化默认设置（仅插入不存在的 key）"""
    session = get_session()
    try:
        for key, info in _DEFAULT_SETTINGS.items():
            existing = session.query(UserSettings).filter(UserSettings.key == key).first()
            if not existing:
                session.add(UserSettings(key=key, value=info["value"], description=info["description"]))
                logger.info(f"初始化默认设置: {key}={info['value']}")
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"初始化默认设置失败: {e}")
    finally:
        session.close()


# 模块导入时自动初始化
_init_default_settings()


@router.get("/settings", summary="获取所有用户设置")
async def get_settings():
    """返回所有用户设置的键值对"""
    session = get_session()
    try:
        rows = session.query(UserSettings).all()
        return {
            row.key: {
                "value": row.value,
                "description": row.description,
                "updated_at": str(row.updated_at) if row.updated_at else None,
            }
            for row in rows
        }
    finally:
        session.close()


@router.put("/settings/{key}", summary="更新指定设置")
async def update_setting(key: str, value: str = Query(..., description="新值")):
    """更新指定 key 的设置值"""
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == key).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"设置项 '{key}' 不存在")
        old_value = row.value
        row.value = value
        session.commit()
        logger.info(f"更新设置: {key} = {old_value} → {value}")
        return {"key": key, "value": value, "message": "更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
