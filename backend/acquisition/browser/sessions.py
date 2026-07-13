"""
抓取会话注册表 + 并发闸门。

背景：BrowserSession 原先所有调用共享同一个 user_data_dir，并发
launch_persistent_context 会因 Chromium 的 profile 锁互相打架（第二个
请求要么启动失败要么行为异常）。这里给每个抓取会话分配独立的 profile
子目录，并用一个信号量闸门统一限制同时存活的浏览器进程数。

同时承担"抓取过程可观测"的职责：discover_api/fetch_api/scrape 在关键
步骤调 report() 上报 stage，供 /knowledge/scrape/stream 转发给前端，
也让 GET /knowledge/scrape/sessions 能查到所有抓取活动（含 MoneyBill
聊天里触发的），不局限于走新流式端点手动发起的那次。

session_id 格式对齐 agents/context.py 的 mb_<12hex> 风格：scr_<12hex>。
"""
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Callable, Optional

from loguru import logger

from acquisition.config import get_playwright_user_data_dir

_MAX_SESSIONS_KEPT = 200  # 内存注册表上限，超过丢最老的（进程内展示用，非持久化审计）

_lock = threading.Lock()
_sessions: dict[str, dict] = {}
_order: list[str] = []  # 按创建顺序，最新的在末尾

_gate = threading.BoundedSemaphore(int(os.getenv("KNOWLEDGE_SCRAPE_MAX_CONCURRENT", "3")))


def new_session_id() -> str:
    return f"scr_{uuid.uuid4().hex[:12]}"


def profile_dir(session_id: str) -> str:
    """该会话独立的浏览器 profile 目录（替代原先所有会话共享的全局目录）。"""
    base = get_playwright_user_data_dir()
    return os.path.join(base, "sessions", session_id)


def register(session_id: str, domain: Optional[str] = None, url: Optional[str] = None) -> None:
    with _lock:
        _sessions[session_id] = {
            "session_id": session_id,
            "domain": domain,
            "url": url,
            "stage": "pending",
            "status": "running",
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _order.append(session_id)
        while len(_order) > _MAX_SESSIONS_KEPT:
            old = _order.pop(0)
            _sessions.pop(old, None)


def report(
    session_id: Optional[str],
    on_progress: Optional[Callable[[str, dict], None]],
    stage: str,
    **fields,
) -> None:
    """更新会话注册表里的 stage，并（若提供）触发 on_progress 回调。"""
    if session_id:
        with _lock:
            rec = _sessions.get(session_id)
            if rec is not None:
                rec["stage"] = stage
                rec["updated_at"] = time.time()
                rec.update(fields)
    if on_progress:
        try:
            on_progress(stage, fields)
        except Exception as e:  # noqa: BLE001 — 进度回调失败不应打断抓取本身
            logger.debug(f"on_progress 回调异常（忽略）：{str(e)[:80]}")


def finish(session_id: Optional[str], status: str, error: Optional[str] = None) -> None:
    if not session_id:
        return
    with _lock:
        rec = _sessions.get(session_id)
        if rec is not None:
            rec["status"] = status
            rec["stage"] = "done" if status == "done" else rec.get("stage", "done")
            rec["error"] = error
            rec["updated_at"] = time.time()


def get_session(session_id: str) -> Optional[dict]:
    with _lock:
        rec = _sessions.get(session_id)
        return dict(rec) if rec else None


def list_sessions(limit: int = 50) -> list[dict]:
    with _lock:
        ids = list(reversed(_order))[:limit]
        return [dict(_sessions[i]) for i in ids if i in _sessions]


@contextmanager
def gate():
    """并发闸门——真正要拉起 Chromium 进程前必须先拿到它（唯一汇聚点）。"""
    _gate.acquire()
    try:
        yield
    finally:
        _gate.release()
