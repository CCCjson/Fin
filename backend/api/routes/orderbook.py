"""
订单簿模拟器 API 代理路由

将前端请求转发到 C++ 订单簿服务 (localhost:8001)。
前端只和 FastAPI(:8000) 通信，不直接访问 C++ 服务。
"""
import asyncio
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

router = APIRouter(prefix="/orderbook", tags=["订单簿模拟器"])

# C++ 订单簿服务地址
CPP_SERVICE_URL = "http://localhost:8001"
TIMEOUT = 10.0


# ── 请求模型 ──

class CreateSessionRequest(BaseModel):
    symbol: str = Field(default="TEST", description="模拟的股票代码")
    mid_price: float = Field(default=100.0, description="初始中间价")
    seed_count: int = Field(default=200, description="初始播种订单数量")
    tick_size: float = Field(default=0.01, description="最小价格变动")
    spread_ticks: int = Field(default=2, description="初始价差(tick数)")


class SubmitOrderRequest(BaseModel):
    side: str = Field(..., description="BUY 或 SELL")
    order_type: str = Field(default="LIMIT", description="MARKET/LIMIT/IOC/FOK")
    price: float = Field(default=0.0, description="价格(市价单为0)")
    quantity: int = Field(default=100, description="数量")


class SeedRequest(BaseModel):
    count: int = Field(default=100, description="播种数量")
    mid_price: float = Field(default=100.0, description="中间价")
    tick_size: float = Field(default=0.01, description="最小价格变动")
    spread_ticks: int = Field(default=2, description="价差")


# ── 代理工具函数（同步 requests，在线程池中运行，不受事件循环阻塞影响） ──

def _proxy_sync(method: str, path: str, body: dict = None) -> dict:
    """转发请求到 C++ 服务（同步版本，运行在线程池）"""
    url = f"{CPP_SERVICE_URL}{path}"
    try:
        if method == "GET":
            resp = requests.get(url, timeout=TIMEOUT)
        elif method == "POST":
            resp = requests.post(url, json=body or {}, timeout=TIMEOUT)
        elif method == "DELETE":
            resp = requests.delete(url, timeout=TIMEOUT)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text or f"C++ 服务返回 {resp.status_code}"
            raise HTTPException(status_code=resp.status_code, detail=detail)

        return resp.json()
    except requests.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="C++ 订单簿服务未启动。请先运行: cd orderbook_simulator/build && ./orderbook_server"
        )
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="C++ 服务响应超时")


async def _proxy(method: str, path: str, body: dict = None) -> dict:
    """转发请求到 C++ 服务（线程池隔离，不受事件循环阻塞影响）"""
    return await asyncio.to_thread(_proxy_sync, method, path, body)


# ── API 端点 ──

@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    """创建订单簿模拟会话"""
    result = await _proxy("POST", "/api/sessions", req.model_dump())
    return result


@router.get("/sessions/{session_id}/depth")
async def get_depth(session_id: str, levels: int = 10):
    """获取盘口深度"""
    result = await _proxy("GET", f"/api/sessions/{session_id}/depth?levels={levels}")
    return result


@router.post("/sessions/{session_id}/orders")
async def submit_order(session_id: str, req: SubmitOrderRequest):
    """提交订单"""
    result = await _proxy("POST", f"/api/sessions/{session_id}/orders", req.model_dump())
    return result


@router.delete("/sessions/{session_id}/orders/{order_id}")
async def cancel_order(session_id: str, order_id: str):
    """撤销订单"""
    result = await _proxy("DELETE", f"/api/sessions/{session_id}/orders/{order_id}")
    return result


@router.get("/sessions/{session_id}/fills")
async def get_fills(session_id: str, limit: int = 50):
    """获取成交记录"""
    result = await _proxy("GET", f"/api/sessions/{session_id}/fills?limit={limit}")
    return result


@router.get("/sessions/{session_id}/stats")
async def get_stats(session_id: str):
    """获取盘口统计"""
    result = await _proxy("GET", f"/api/sessions/{session_id}/stats")
    return result


@router.post("/sessions/{session_id}/seed")
async def seed_orders(session_id: str, req: SeedRequest):
    """随机播种限价单"""
    result = await _proxy("POST", f"/api/sessions/{session_id}/seed", req.model_dump())
    return result
