"""
WebSocket 端点 — 自动化交易实时推送
"""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from automation.websocket_manager import ws_manager

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/automation")
async def automation_websocket(websocket: WebSocket):
    """
    自动化交易 WebSocket 端点

    客户端连接后会收到实时推送：
    - pending_order: 新的待确认订单
    - order_confirmed/filled/rejected/expired: 订单状态
    - risk_alert: 风控告警
    - scan_started/completed: 扫描状态
    - scheduler_status: 调度器状态
    - pong: 心跳响应
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                # 处理心跳
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await ws_manager.send_personal(websocket, {"type": "pong"})
                except (json.JSONDecodeError, TypeError):
                    if data == "ping":
                        await ws_manager.send_personal(websocket, {"type": "pong"})
            except asyncio.TimeoutError:
                # 超时发送 ping 检测连接
                try:
                    await ws_manager.send_personal(websocket, {"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WebSocket 异常: {e}")
    finally:
        await ws_manager.disconnect(websocket)
