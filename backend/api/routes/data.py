"""
数据相关API
"""
import asyncio
import os
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
import pandas as pd
from datetime import datetime
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import or_

from api.routes._stream_utils import bridge_sync_stream
from api.models.schemas import (
    StockDataRequest,
    StockDataResponse,
    StockListResponse,
    MessageResponse
)
from data_engine import DataEngine
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo, UserSettings
from common.market_time import utc_iso

router = APIRouter(prefix="/data", tags=["数据"])

# 全局数据引擎实例
data_engine = DataEngine()


@router.post("/daily", response_model=StockDataResponse)
async def get_daily_data(request: StockDataRequest):
    """
    获取日线数据

    db_only=true 时仅查数据库缓存（毫秒级），不联网拉取。
    db_only=false 时若数据不够新会联网补齐（可能数秒）。
    """
    try:
        loop = asyncio.get_event_loop()

        # 同步 IO 放到线程池，避免阻塞事件循环
        df = await loop.run_in_executor(
            None,
            lambda: data_engine.get_daily_data(
                symbol=request.symbol,
                start_date=request.start_date,
                end_date=request.end_date,
                db_only=request.db_only,
            ),
        )

        if df.empty:
            if request.db_only:
                # db_only 模式下没数据不算错，返回空
                return StockDataResponse(symbol=request.symbol, data=[], count=0)
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stocks", response_model=StockListResponse)
async def get_stock_list(market: str = "a_share"):
    """
    获取指定市场的股票列表

    Args:
        market: 市场标识，任意写法（a_share/hk_stock/us_stock、A/HK/US 等）会归一到 canonical
    """
    try:
        from common.market import normalize_market
        canonical = normalize_market(market)
        rows = data_engine.stock_repo.get_stocks_by_market(canonical)
        stocks = [
            {"symbol": s.symbol, "name": s.name or "", "market": s.market or canonical}
            for s in rows
        ]
        return StockListResponse(stocks=stocks, count=len(stocks))

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

        # 用线程+队列桥接：阻塞式抓取跑在 daemon 线程里，不占事件循环，
        # 避免全市场更新把整个后端 API 卡死。
        return StreamingResponse(
            bridge_sync_stream(sync_gen),
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


# ==================== 统一行情刷新（三市场一个入口） ====================

class MarketRefreshRequest(BaseModel):
    """统一行情刷新请求。

    `scope`：市场名列表（如 `["a_share"]` 只刷 A 股），省略/空 = 三个市场都刷。
    每个市场内部自动检查更新到哪天、只补落后的、差一天直接补一天 ——
    这层不预筛，判定精度交给各 updater（见 data_engine/market_refresh.py）。
    """
    scope: list[str] | None = None


@router.get("/freshness", summary="查询三市场行情新鲜度")
async def get_market_freshness_ep():
    """三个市场各自更新到哪个交易日、是否落后（判定内核在 common/market_freshness）。

    只读、不触发更新 —— 前端「要不要点刷新」的依据。
    """
    try:
        from data_engine.health import get_freshness
        session = get_session()
        try:
            return await asyncio.to_thread(get_freshness, session)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"查询行情新鲜度失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh/stream", summary="统一流式刷新三市场行情")
async def market_refresh_stream(request: MarketRefreshRequest | None = None):
    """一个入口刷新 A 股 + 港股 + 美股日线（流式进度，事件带 market 字段区分市场）。

    已到最新交易日的市场内部秒回（不白跑全量）；港美股共抢一次 Yahoo 互斥锁。
    """
    try:
        from data_engine.market_refresh import refresh_stream, resolve_scope

        scope = request.scope if request else None
        # refresh_stream 是生成器（惰性），未知市场的 ValueError 要迭代才抛 —— 那时
        # StreamingResponse 已经开始流、变不成 400 了。所以在这里提前校验一次。
        resolve_scope(scope)
        return StreamingResponse(
            bridge_sync_stream(refresh_stream(scope)),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except ValueError as e:      # 未知市场名 → 400，不是 500
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"统一行情刷新失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 全市场财务数据回补 ====================

class FinancialBackfillRequest(BaseModel):
    """财务回补请求参数"""
    mode: str = "incremental"   # incremental=跳过近 stale_days 内有报告的 | full=全量
    stale_days: int = 150
    # 默认并发度：走 env FINANCIAL_FETCH_WORKERS（默认 3），一个旋钮同时管默认值；
    # 实际仍会被 fetcher 侧 _MAX_WORKERS 上限钳制。请求体显式传 workers 可覆盖。
    workers: int = int(os.getenv("FINANCIAL_FETCH_WORKERS", "3"))
    start_year: str = "2015"


@router.post("/financial/backfill/stream", summary="流式补齐全市场财务数据")
async def financial_backfill_stream(request: FinancialBackfillRequest):
    """
    增量/全量补齐全市场 A 股财务数据（流式进度）

    直连新浪财务指标接口 + 多代理 IP 并发；增量模式自动跳过
    近期已有报告期的股票。同一时间只允许一个回补在跑（busy 时
    流内返回 error 事件）。
    """
    try:
        from data_engine.financial_updater import FinancialUpdater

        updater = FinancialUpdater()
        sync_gen = updater.update_stream(
            mode=request.mode,
            stale_days=request.stale_days,
            start_year=request.start_year,
            workers=request.workers,
        )
        return StreamingResponse(
            bridge_sync_stream(sync_gen),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.error(f"财务数据回补失败: {e}")
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
                StockInfo.is_active.is_(True),
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


@router.get("/stocks/names", summary="按代码列表批量查名称")
async def get_stocks_names(
    symbols: str = Query(..., description="逗号分隔的股票代码列表，如 600519.SH,AAPL"),
):
    """
    按 symbol 列表批量查名称，供前端"记录表本身没存 name"的场景按需查询。
    返回 {symbol: name} dict，查不到的 symbol 不出现在结果里。
    """
    from data_engine.storage.repository import get_stock_names

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()][:200]
    session = get_session()
    try:
        return get_stock_names(session, symbol_list)
    finally:
        session.close()


# ==================== 用户设置 ====================

# 默认设置（启动时自动写入）
_DEFAULT_SETTINGS = {
    "total_capital": {"value": "5000", "description": "总资金（元），用于计算仓位占比与建议买入金额/股数"},
    # ⚠️ 别写「1.0 = all-in」：总仓位 ≤ 80%（留 20% 现金）是抬不动的硬底线，
    # 设 1.0 只等于「取消单股约束」，实际上界仍是 80%（见 trading_engine/risk/adapter.py）。
    "max_position_pct": {"value": "0.2", "description": "单股最大仓位占比（集中度）：0.2 分散 / 0.5 集中 / 1.0 取消单股约束（上界仍受总仓位 80% 硬底线约束，20% 现金必留）"},
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
                "updated_at": utc_iso(row.updated_at),
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
