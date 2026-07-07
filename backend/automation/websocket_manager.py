"""
WebSocket 连接管理器 — 自动化交易实时推送

消息类型:
- pending_order: 新的待确认订单
- order_confirmed: 订单已确认执行
- order_filled: 订单已成交
- order_rejected: 订单被拒绝
- order_expired: 订单已过期
- risk_alert: 风控告警
- scan_started: 扫描开始
- scan_completed: 扫描完成
- scheduler_status: 调度器状态变更
"""
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Set

from fastapi import WebSocket
from loguru import logger


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"WebSocket 客户端连接，当前连接数: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        """断开连接"""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"WebSocket 客户端断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """广播消息到所有连接"""
        if not self.active_connections:
            return

        data = json.dumps(message, ensure_ascii=False, default=str)
        disconnected = []

        for conn in self.active_connections:
            try:
                await conn.send_text(data)
            except Exception:
                disconnected.append(conn)

        # 清理断开的连接
        if disconnected:
            async with self._lock:
                for conn in disconnected:
                    if conn in self.active_connections:
                        self.active_connections.remove(conn)

    async def send_personal(self, websocket: WebSocket, message: Dict[str, Any]):
        """发送消息到指定连接"""
        try:
            data = json.dumps(message, ensure_ascii=False, default=str)
            await websocket.send_text(data)
        except Exception as e:
            logger.error(f"发送消息失败: {e}")

    @property
    def connection_count(self) -> int:
        return len(self.active_connections)


# 全局实例
ws_manager = ConnectionManager()


# -------- 便捷广播方法 --------

async def notify_pending_order(order: Dict[str, Any]):
    """通知：新的待确认订单"""
    await ws_manager.broadcast({
        "type": "pending_order",
        "data": order,
        "timestamp": datetime.now().isoformat(),
    })


async def notify_order_status(order_id: str, status: str, order: Dict[str, Any]):
    """通知：订单状态变更"""
    await ws_manager.broadcast({
        "type": f"order_{status.lower()}",
        "data": {"order_id": order_id, "status": status, **order},
        "timestamp": datetime.now().isoformat(),
    })
    # 成交事件汇入业务总线（自动化路径）
    if status.lower() == "filled":
        try:
            from business_events import publish_event, ORDER_FILLED
            sym = order.get("symbol")
            publish_event(
                ORDER_FILLED, source="automation", symbol=sym,
                title=f"自动化成交 {order.get('name') or sym or order_id}",
                order_id=order_id, **{k: order.get(k) for k in ("side", "price", "quantity") if k in order},
            )
        except Exception:  # noqa: BLE001
            pass


async def notify_risk_alert(symbol: str, alert_type: str, message: str, detail: Dict = None):
    """通知：风控告警"""
    await ws_manager.broadcast({
        "type": "risk_alert",
        "data": {
            "symbol": symbol,
            "alert_type": alert_type,
            "message": message,
            "detail": detail or {},
        },
        "timestamp": datetime.now().isoformat(),
    })
    try:
        from business_events import publish_event, RISK_ALERT
        publish_event(RISK_ALERT, source="automation", symbol=symbol, severity="warn",
                      title=f"风控告警：{message}", alert_type=alert_type)
    except Exception:  # noqa: BLE001
        pass


async def notify_scan_started(config_id: str, scan_type: str, symbols_count: int):
    """通知：扫描开始"""
    await ws_manager.broadcast({
        "type": "scan_started",
        "data": {
            "config_id": config_id,
            "scan_type": scan_type,
            "symbols_count": symbols_count,
        },
        "timestamp": datetime.now().isoformat(),
    })


async def notify_scan_completed(config_id: str, scan_type: str, result: Dict):
    """通知：扫描完成"""
    await ws_manager.broadcast({
        "type": "scan_completed",
        "data": {
            "config_id": config_id,
            "scan_type": scan_type,
            **result,
        },
        "timestamp": datetime.now().isoformat(),
    })
    try:
        from business_events import publish_event, SCAN_COMPLETED
        found = result.get("signals_found") or result.get("orders_created")
        publish_event(SCAN_COMPLETED, source="automation",
                      title=f"扫描完成（{scan_type}）" + (f"，发现 {found}" if found else ""),
                      config_id=config_id, scan_type=scan_type,
                      signals_found=result.get("signals_found"),
                      orders_created=result.get("orders_created"))
    except Exception:  # noqa: BLE001
        pass


async def notify_scheduler_status(running: bool, message: str = ""):
    """通知：调度器状态"""
    await ws_manager.broadcast({
        "type": "scheduler_status",
        "data": {"running": running, "message": message},
        "timestamp": datetime.now().isoformat(),
    })
