"""
股票池 API — 提供指数成分股和行业板块股票列表

取数与缓存逻辑已下沉到 data_engine/stock_pools.py，此处仅做 HTTP 封装。
"""
import asyncio
from fastapi import APIRouter, HTTPException
from loguru import logger

from data_engine.stock_pools import (
    INDEX_POOLS,
    get_cached as _get_cached,
    set_cached as _set_cached,
    fetch_index_constituents as _fetch_index_constituents,
    fetch_industry_list as _fetch_industry_list,
    fetch_industry_stocks as _fetch_industry_stocks,
)

router = APIRouter(prefix="/stock_pools", tags=["股票池"])


# ── API 端点 ──

@router.get("/pools")
async def list_pools():
    """列出可用的股票池"""
    pools = []
    for pool_id, info in INDEX_POOLS.items():
        pools.append({
            "id": pool_id,
            "name": info["name"],
            "description": info["description"],
            "type": "index",
        })
    pools.append({
        "id": "industry",
        "name": "行业板块",
        "description": "按行业筛选股票",
        "type": "industry",
    })
    return {"pools": pools}


@router.get("/pools/{pool_id}")
async def get_pool_stocks(pool_id: str):
    """获取指数成分股"""
    if pool_id not in INDEX_POOLS:
        raise HTTPException(status_code=404, detail=f"未知的股票池: {pool_id}")

    cache_key = f"index_{pool_id}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    info = INDEX_POOLS[pool_id]
    try:
        stocks = await asyncio.wait_for(
            asyncio.to_thread(_fetch_index_constituents, info["index_code"]),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="获取成分股超时，请稍后重试")

    result = {
        "pool_id": pool_id,
        "name": info["name"],
        "count": len(stocks),
        "stocks": stocks,
    }
    if stocks:
        _set_cached(cache_key, result)
    return result


@router.get("/industries")
async def list_industries():
    """列出行业板块"""
    cache_key = "industry_list"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        industries = await asyncio.wait_for(
            asyncio.to_thread(_fetch_industry_list),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="获取行业列表超时，请稍后重试")
    result = {"industries": industries, "count": len(industries)}
    if industries:
        _set_cached(cache_key, result)
    return result


@router.get("/industries/{name}")
async def get_industry_stocks(name: str):
    """获取行业板块成分股"""
    cache_key = f"industry_{name}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        stocks = await asyncio.wait_for(
            asyncio.to_thread(_fetch_industry_stocks, name),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="获取行业股票超时，请稍后重试")
    result = {
        "industry": name,
        "count": len(stocks),
        "stocks": stocks,
    }
    if stocks:
        _set_cached(cache_key, result)
    return result
