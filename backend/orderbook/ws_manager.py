"""
订单簿 WebSocket 连接管理器

按 session_id 分组管理连接，支持：
- 事件驱动推送（下单/撤单后立即推送）
- 定时兜底推送（500ms 轮询 diff）
- 增量 fills 推送（只推新成交）
"""
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Any, Optional

from fastapi import WebSocket
from loguru import logger


class OrderBookWSManager:
    """按 session_id 分组的 WebSocket 连接管理器"""

    def __init__(self):
        # session_id -> [WebSocket, ...]
        self._connections: Dict[str, List[WebSocket]] = {}
        self._lock = asyncio.Lock()
        # session_id -> 上次推送的快照（用于 diff）
        self._last_snapshot: Dict[str, dict] = {}
        # session_id -> 上次推送时的 total_fills 数
        self._last_fill_count: Dict[str, int] = {}
        # 全局序列号
        self._seq = 0

    async def connect(self, session_id: str, ws: WebSocket):
        """接受新连接并加入 session 分组"""
        await ws.accept()
        async with self._lock:
            if session_id not in self._connections:
                self._connections[session_id] = []
            self._connections[session_id].append(ws)
        logger.info(f"OrderBook WS 连接: session={session_id[:12]}..., "
                     f"该会话连接数: {len(self._connections.get(session_id, []))}")

    async def disconnect(self, session_id: str, ws: WebSocket):
        """断开连接"""
        async with self._lock:
            conns = self._connections.get(session_id, [])
            if ws in conns:
                conns.remove(ws)
            if not conns and session_id in self._connections:
                del self._connections[session_id]
                self._last_snapshot.pop(session_id, None)
                self._last_fill_count.pop(session_id, None)
        logger.info(f"OrderBook WS 断开: session={session_id[:12]}...")

    def has_subscribers(self, session_id: str) -> bool:
        """检查某 session 是否有活跃连接"""
        return bool(self._connections.get(session_id))

    def active_sessions(self) -> List[str]:
        """返回有活跃 WS 连接的所有 session_id"""
        return list(self._connections.keys())

    async def broadcast(self, session_id: str, data: dict):
        """广播消息到指定 session 的所有客户端"""
        conns = self._connections.get(session_id, [])
        if not conns:
            return

        self._seq += 1
        message = {
            "type": "orderbook_update",
            "session_id": session_id,
            "data": data,
            "seq": self._seq,
            "timestamp": datetime.now().isoformat(),
        }
        payload = json.dumps(message, ensure_ascii=False, default=str)

        disconnected = []
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(ws)

        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    if ws in self._connections.get(session_id, []):
                        self._connections[session_id].remove(ws)

    def should_push(self, session_id: str, new_snapshot: dict) -> bool:
        """对比快照，判断是否有变化需要推送"""
        old = self._last_snapshot.get(session_id)
        if old is None:
            return True

        # 对比 mid_price、spread、总成交量
        old_stats = old.get("stats", {})
        new_stats = new_snapshot.get("stats", {})

        if old_stats.get("mid_price") != new_stats.get("mid_price"):
            return True
        if old_stats.get("spread") != new_stats.get("spread"):
            return True
        if old_stats.get("total_fills") != new_stats.get("total_fills"):
            return True
        if old_stats.get("bid_depth") != new_stats.get("bid_depth"):
            return True
        if old_stats.get("ask_depth") != new_stats.get("ask_depth"):
            return True

        return False

    def update_snapshot(self, session_id: str, snapshot: dict):
        """更新缓存的快照"""
        self._last_snapshot[session_id] = snapshot

    def get_new_fills(self, session_id: str, all_fills: list, total: Optional[int] = None) -> list:
        """返回自上次推送后的新增成交

        Args:
            all_fills: C++ 返回的成交列表（仅最近 limit 条的尾窗，会被截断）
            total: 该会话的全量历史成交笔数（C++ 响应里的 total）。必须用它做增量判断，
                   不能用 len(all_fills)——后者被 limit 截断后会饱和（如恒为 50），
                   导致累计超过 limit 后再也判定不出新成交。
        """
        if total is None:
            total = len(all_fills)
        last_total = self._last_fill_count.get(session_id, 0)
        self._last_fill_count[session_id] = total
        new_count = total - last_total
        if new_count <= 0:
            return []
        # 尾窗最多只有 len(all_fills) 条；新增数超过尾窗（极端高频）时只能补到尾窗全部
        if new_count >= len(all_fills):
            return all_fills
        return all_fills[len(all_fills) - new_count:]

    @property
    def total_connections(self) -> int:
        return sum(len(conns) for conns in self._connections.values())


# 全局实例
ob_ws_manager = OrderBookWSManager()
