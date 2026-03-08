"""
股票池 API — 提供指数成分股和行业板块股票列表
"""
import asyncio
import time
from fastapi import APIRouter, HTTPException
from typing import Dict, List, Tuple, Any
from loguru import logger

router = APIRouter(prefix="/stock_pools", tags=["股票池"])

# ── 简单内存缓存 ──
_cache: Dict[str, Tuple[Any, float]] = {}
CACHE_TTL = 3600 * 12  # 12 小时


def _get_cached(key: str):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _set_cached(key: str, data):
    _cache[key] = (data, time.time())


def _add_exchange_suffix(code: str) -> str:
    """给纯数字股票代码添加交易所后缀"""
    code = code.strip()
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    elif code.startswith(("0", "3", "2")):
        return f"{code}.SZ"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    return code


# ── 预设指数池 ──

INDEX_POOLS = {
    "sse50": {"name": "上证50", "index_code": "000016", "description": "上交所 50 只核心蓝筹"},
    "csi300": {"name": "沪深300", "index_code": "000300", "description": "沪深两市 300 只大盘股"},
    "csi500": {"name": "中证500", "index_code": "000905", "description": "中盘成长股 500 只"},
}


def _fetch_index_constituents(index_code: str) -> List[Dict[str, str]]:
    """获取指数成分股（同步，在线程池中运行）"""
    import akshare as ak

    try:
        df = ak.index_stock_cons(symbol=index_code)
        if df is None or df.empty:
            return []

        # 自适应列名
        code_col = None
        name_col = None
        for col in df.columns:
            if "代码" in col or "code" in col.lower():
                code_col = col
            if "名称" in col or "name" in col.lower():
                name_col = col
        if not code_col:
            code_col = df.columns[0]
        if not name_col and len(df.columns) > 1:
            name_col = df.columns[1]

        result = []
        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            name = str(row[name_col]).strip() if name_col else ""
            symbol = _add_exchange_suffix(code)
            result.append({"symbol": symbol, "name": name})

        return result
    except Exception as e:
        logger.error(f"获取指数 {index_code} 成分股失败: {e}")
        return []


def _fetch_industry_list() -> List[Dict[str, str]]:
    """获取行业板块列表"""
    import akshare as ak

    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return []

        name_col = None
        for col in df.columns:
            if "板块名称" in col or "名称" in col:
                name_col = col
                break
        if not name_col:
            name_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

        return [{"name": str(row[name_col]).strip()} for _, row in df.iterrows()]
    except Exception as e:
        logger.error(f"获取行业板块列表失败: {e}")
        return []


def _fetch_industry_stocks(name: str) -> List[Dict[str, str]]:
    """获取行业板块成分股"""
    import akshare as ak

    try:
        df = ak.stock_board_industry_cons_em(symbol=name)
        if df is None or df.empty:
            return []

        code_col = None
        name_col = None
        for col in df.columns:
            if "代码" in col:
                code_col = col
            if "名称" in col:
                name_col = col
        if not code_col:
            code_col = df.columns[0]
        if not name_col and len(df.columns) > 1:
            name_col = df.columns[1]

        result = []
        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            stock_name = str(row[name_col]).strip() if name_col else ""
            symbol = _add_exchange_suffix(code)
            result.append({"symbol": symbol, "name": stock_name})
        return result
    except Exception as e:
        logger.error(f"获取行业 {name} 成分股失败: {e}")
        return []


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
