"""
订单簿模拟器 API 路由

三大优化集成：
1. httpx.AsyncClient 替代同步 requests（连接池复用）
2. WebSocket 实时推送（事件驱动 + 定时兜底）
3. 自动做市商 Bot API
"""
import asyncio
from dataclasses import asdict
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from loguru import logger

from orderbook.ws_manager import ob_ws_manager
from orderbook.market_maker import mm_manager, MarketMakerConfig

router = APIRouter(prefix="/orderbook", tags=["订单簿模拟器"])

# C++ 订单簿服务地址
CPP_SERVICE_URL = "http://localhost:8001"

# 全局 httpx 异步客户端（连接池复用）
_client: Optional[httpx.AsyncClient] = None

# 后台推送 task 句柄（保存以便 shutdown 时取消，避免泄漏 / reload 堆积孤儿循环）
_bg_task: Optional[asyncio.Task] = None


async def get_client() -> httpx.AsyncClient:
    """获取/初始化全局 httpx 客户端"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=CPP_SERVICE_URL,
            timeout=10.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client():
    """关闭全局客户端（应用关闭时调用）"""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


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


class MarketMakerStartRequest(BaseModel):
    interval_ms: int = Field(default=300, description="报价周期(ms)")
    spread_ticks: int = Field(default=2, description="半价差tick数")
    levels: int = Field(default=3, description="每侧档数")
    base_quantity: int = Field(default=500, description="首档数量")
    quantity_decay: float = Field(default=0.6, description="数量衰减系数")
    max_position: int = Field(default=10000, description="最大净持仓")
    skew_factor: float = Field(default=0.001, description="库存偏斜系数")
    price_drift: float = Field(default=0.0, description="价格漂移")
    volatility: float = Field(default=0.0005, description="波动幅度")
    tick_size: float = Field(default=0.01, description="最小价格变动")


class MarketMakerConfigUpdate(BaseModel):
    interval_ms: Optional[int] = None
    spread_ticks: Optional[int] = None
    levels: Optional[int] = None
    base_quantity: Optional[int] = None
    quantity_decay: Optional[float] = None
    max_position: Optional[int] = None
    skew_factor: Optional[float] = None
    price_drift: Optional[float] = None
    volatility: Optional[float] = None


# ── 代理工具函数（httpx 异步版） ──

async def _proxy(method: str, path: str, body: dict = None) -> dict:
    """异步转发请求到 C++ 服务"""
    client = await get_client()
    try:
        resp = await client.request(method, path, json=body)

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text or f"C++ 服务返回 {resp.status_code}"
            raise HTTPException(status_code=resp.status_code, detail=detail)

        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="C++ 订单簿服务未启动。请先运行: cd orderbook_simulator/build && ./orderbook_server"
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="C++ 服务响应超时")


# ── WebSocket 推送辅助 ──

async def _push_snapshot(session_id: str):
    """拉取最新数据并推送给 WebSocket 客户端"""
    if not ob_ws_manager.has_subscribers(session_id):
        return

    try:
        client = await get_client()
        depth_resp, fills_resp, stats_resp = await asyncio.gather(
            client.get(f"/api/sessions/{session_id}/depth?levels=15"),
            client.get(f"/api/sessions/{session_id}/fills?limit=50"),
            client.get(f"/api/sessions/{session_id}/stats"),
            return_exceptions=True,
        )

        snapshot = {}
        if not isinstance(depth_resp, Exception) and depth_resp.status_code == 200:
            snapshot["depth"] = depth_resp.json()
        if not isinstance(stats_resp, Exception) and stats_resp.status_code == 200:
            snapshot["stats"] = stats_resp.json()

        # 增量 fills：以 C++ 返回的全量笔数 total 做增量判断
        # （fills 列表被 ?limit=50 截断为尾窗，不能用其长度判断新增）
        all_fills = []
        total_fills = None
        if not isinstance(fills_resp, Exception) and fills_resp.status_code == 200:
            fills_data = fills_resp.json()
            all_fills = fills_data.get("fills", [])
            total_fills = fills_data.get("total")
        new_fills = ob_ws_manager.get_new_fills(session_id, all_fills, total_fills)
        snapshot["fills"] = new_fills

        ob_ws_manager.update_snapshot(session_id, snapshot)
        await ob_ws_manager.broadcast(session_id, snapshot)
    except Exception as e:
        logger.debug(f"WS 推送失败: {e}")


async def ws_background_task():
    """后台定时推送 task（500ms 间隔，diff 推送）"""
    while True:
        try:
            sessions = ob_ws_manager.active_sessions()
            if sessions:
                client = await get_client()
                for sid in sessions:
                    try:
                        # 拉取 stats 做 diff 判断
                        stats_resp = await client.get(f"/api/sessions/{sid}/stats")
                        if stats_resp.status_code != 200:
                            continue

                        test_snapshot = {"stats": stats_resp.json()}
                        if ob_ws_manager.should_push(sid, test_snapshot):
                            await _push_snapshot(sid)
                    except Exception:
                        pass
        except Exception:
            pass

        await asyncio.sleep(0.5)


def start_bg_task():
    """启动后台推送 task 并保存句柄（startup 时调用）"""
    global _bg_task
    if _bg_task is None or _bg_task.done():
        _bg_task = asyncio.create_task(ws_background_task())
    return _bg_task


async def stop_bg_task():
    """取消后台推送 task（shutdown 时调用，先于 close_client）"""
    global _bg_task
    if _bg_task is not None and not _bg_task.done():
        _bg_task.cancel()
        try:
            await _bg_task
        except asyncio.CancelledError:
            pass
    _bg_task = None


# ── API 端点 ──

@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    """创建订单簿模拟会话"""
    return await _proxy("POST", "/api/sessions", req.model_dump())


@router.get("/sessions/{session_id}/depth")
async def get_depth(session_id: str, levels: int = 10):
    """获取盘口深度"""
    return await _proxy("GET", f"/api/sessions/{session_id}/depth?levels={levels}")


@router.post("/sessions/{session_id}/orders")
async def submit_order(session_id: str, req: SubmitOrderRequest):
    """提交订单"""
    result = await _proxy("POST", f"/api/sessions/{session_id}/orders", req.model_dump())
    # 事件驱动推送
    asyncio.create_task(_push_snapshot(session_id))
    return result


@router.delete("/sessions/{session_id}/orders/{order_id}")
async def cancel_order(session_id: str, order_id: str):
    """撤销订单"""
    result = await _proxy("DELETE", f"/api/sessions/{session_id}/orders/{order_id}")
    asyncio.create_task(_push_snapshot(session_id))
    return result


@router.get("/sessions/{session_id}/fills")
async def get_fills(session_id: str, limit: int = 50):
    """获取成交记录"""
    return await _proxy("GET", f"/api/sessions/{session_id}/fills?limit={limit}")


@router.get("/sessions/{session_id}/stats")
async def get_stats(session_id: str):
    """获取盘口统计"""
    return await _proxy("GET", f"/api/sessions/{session_id}/stats")


@router.post("/sessions/{session_id}/seed")
async def seed_orders(session_id: str, req: SeedRequest):
    """随机播种限价单"""
    result = await _proxy("POST", f"/api/sessions/{session_id}/seed", req.model_dump())
    asyncio.create_task(_push_snapshot(session_id))
    return result


# ── WebSocket 端点 ──

@router.websocket("/sessions/{session_id}/ws")
async def orderbook_ws(websocket: WebSocket, session_id: str):
    """订单簿实时数据 WebSocket"""
    await ob_ws_manager.connect(session_id, websocket)

    # 连接后立即推送一次完整快照
    try:
        await _push_snapshot(session_id)
    except Exception:
        pass

    try:
        while True:
            # 等待客户端消息（心跳/关闭）
            data = await websocket.receive_text()
            # 客户端可以发 ping，我们回 pong
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await ob_ws_manager.disconnect(session_id, websocket)


# ── 做市商 API ──

@router.post("/sessions/{session_id}/market-maker/start")
async def start_market_maker(session_id: str, req: MarketMakerStartRequest):
    """启动做市商 Bot"""
    config = MarketMakerConfig(
        enabled=True,
        interval_ms=req.interval_ms,
        spread_ticks=req.spread_ticks,
        levels=req.levels,
        base_quantity=req.base_quantity,
        quantity_decay=req.quantity_decay,
        max_position=req.max_position,
        skew_factor=req.skew_factor,
        price_drift=req.price_drift,
        volatility=req.volatility,
        tick_size=req.tick_size,
    )
    status = await mm_manager.start_bot(session_id, config)
    return asdict(status)


@router.post("/sessions/{session_id}/market-maker/stop")
async def stop_market_maker(session_id: str):
    """停止做市商 Bot"""
    status = await mm_manager.stop_bot(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="该会话没有运行中的做市商")
    return asdict(status)


@router.get("/sessions/{session_id}/market-maker/status")
async def get_market_maker_status(session_id: str):
    """获取做市商状态"""
    status = mm_manager.get_status(session_id)
    if status is None:
        return {"running": False}
    return asdict(status)


@router.put("/sessions/{session_id}/market-maker/config")
async def update_market_maker_config(session_id: str, req: MarketMakerConfigUpdate):
    """更新做市商参数（运行中可调）"""
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有提供要更新的参数")

    success = mm_manager.update_config(session_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="该会话没有运行中的做市商")

    config = mm_manager.get_config(session_id)
    return {"message": "配置已更新", "config": config}
