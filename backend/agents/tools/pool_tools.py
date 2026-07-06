"""
股票池类工具 —— 包 api/routes/stock_pools.py 的成分股获取。

复用其模块级同步函数与 12h 内存缓存（_get_cached/_set_cached）。走 akshare，较慢，
在 orchestrator 工作线程里阻塞是安全的。返回精简列表（截断控制 token）。
"""
from typing import Literal

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope


class ListStockPoolArgs(BaseModel):
    pool_id: Literal["sse50", "csi300", "csi500"] = Field(..., description="指数池 id")


@tool(
    name="list_stock_pool",
    description=(
        "列出指数成分股：pool_id 取 sse50(上证50)/csi300(沪深300)/csi500(中证500)。"
        "用户问「沪深300有哪些股票、从蓝筹里选」等圈定选股范围时调用。"
    ),
    args_model=ListStockPoolArgs,
    category="data",
    group="screener",
)
def list_stock_pool(pool_id: str) -> ToolEnvelope:
    from data_engine.stock_pools import (
        INDEX_POOLS,
        fetch_index_constituents as _fetch_index_constituents,
        get_cached as _get_cached,
        set_cached as _set_cached,
    )
    info = INDEX_POOLS.get(pool_id)
    if not info:
        return ToolEnvelope(business_result="negative",
                             message=f"未知股票池 {pool_id}，可选 sse50/csi300/csi500")

    cache_key = f"index_{pool_id}"
    stocks = _get_cached(cache_key)
    if stocks is None:
        stocks = _fetch_index_constituents(info["index_code"])
        if stocks:
            _set_cached(cache_key, stocks)

    return ToolEnvelope(data={
        "pool_id": pool_id,
        "name": info["name"],
        "count": len(stocks),
        "sample": [f"{s['name']}({s['symbol']})" for s in stocks[:20]],
    })


class ListIndustryStocksArgs(BaseModel):
    industry_name: str = Field(..., min_length=1, description="行业板块名称，如 白酒")


@tool(
    name="list_industry_stocks",
    description="列出某行业板块的成分股，如「白酒」「半导体」「银行」。用户想按行业筛股时调用。",
    args_model=ListIndustryStocksArgs,
    category="data",
    group="screener",
)
def list_industry_stocks(industry_name: str) -> ToolEnvelope:
    from data_engine.stock_pools import (
        fetch_industry_stocks as _fetch_industry_stocks,
        get_cached as _get_cached,
        set_cached as _set_cached,
    )
    cache_key = f"industry_{industry_name}"
    stocks = _get_cached(cache_key)
    if stocks is None:
        stocks = _fetch_industry_stocks(industry_name)
        if stocks:
            _set_cached(cache_key, stocks)

    if not stocks:
        return ToolEnvelope(business_result="negative",
                             message=f"没找到行业「{industry_name}」的成分股，请确认板块名称。")
    return ToolEnvelope(data={
        "industry": industry_name,
        "count": len(stocks),
        "sample": [f"{s['name']}({s['symbol']})" for s in stocks[:20]],
    })
