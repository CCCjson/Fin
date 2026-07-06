"""
会话状态 — AgentSession + 内存会话池（参考 advisor_engine/service.py 的 ConversationSession）。

持久化：turn 结束由 API 层调 STORE.save() 落 JSON 到 data/agent_sessions/，
get() miss 时从盘复活 —— 进程重启不再丢对话上下文。
pending_tool_call / turn_monitor / 锁 不落盘：待确认的下单跨重启一律作废（fail-safe）。
回滚开关：AGENT_SESSION_PERSIST=off。
"""
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger

_PERSIST_DIR = Path(__file__).parent.parent / "data" / "agent_sessions"
_PERSIST_MAX_AGE_DAYS = 14  # 落盘文件保留天数，过期由 cleanup 顺带回收
_DISK_CLEANUP_INTERVAL_S = 600  # 磁盘落盘文件 GC 节流间隔——不需要每条消息都扫一次目录
# session_id 只可能是本进程生成的 mb_<12hex>；load 前必须校验，
# 防止把用户可控的 session_id 拼进文件路径（路径穿越）。
_SID_RE = re.compile(r"^mb_[0-9a-f]{12}$")


def persist_enabled() -> bool:
    return os.getenv("AGENT_SESSION_PERSIST", "on").lower() not in ("off", "0", "false")


@dataclass
class PendingToolCall:
    """等待用户二次确认的工具调用（下单类）。"""
    id: str
    name: str
    args: dict


@dataclass
class AgentSession:
    session_id: str
    messages: list[dict] = field(default_factory=list)  # OpenAI 格式 messages
    allowed_tools: Optional[set[str]] = None            # None=全部可见
    pending_tool_call: Optional[PendingToolCall] = None
    page_context: Optional[dict] = None                 # 当前页面上下文
    last_page_sig: Optional[str] = None                 # 上次页面快照签名（去重用）
    turn_monitor: Optional[Any] = None                  # agents.turn_monitor.TurnMonitor（Any 避循环导入）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turn_start_idx: int = 0                             # 本 turn 首条消息下标（trace 用）
    # 同一 session 同时只允许一个 turn 在跑：并发会踩坏 messages 的 tool 配对
    # 与 pending_tool_call（钱路状态）。抢不到锁的请求直接被拒，不排队。
    run_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 协作取消：客户端断开时由 API 层置位，orchestrator 轮首检查
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionStore:
    """内存会话池，带过期清理（参考 AdvisorService）。"""

    def __init__(self, max_age: int = 3600) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.Lock()
        self._max_age = max_age
        self._last_disk_cleanup_at = 0.0  # 磁盘 GC 节流用，见 _cleanup_disk()

    def create(self) -> AgentSession:
        sid = f"mb_{uuid.uuid4().hex[:12]}"
        sess = AgentSession(session_id=sid)
        with self._lock:
            self._cleanup_locked()
            self._sessions[sid] = sess
        return sess

    def get(self, session_id: str) -> Optional[AgentSession]:
        with self._lock:
            sess = self._sessions.get(session_id)
        if sess is not None:
            return sess
        # 内存 miss → 尝试从盘复活（进程重启/内存过期后续接对话）
        sess = self._load_from_disk(session_id)
        if sess is None:
            return None
        with self._lock:
            # 双检：并发复活时保留先入者，避免两份同 id 对象并存
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            self._sessions[session_id] = sess
        return sess

    def get_or_create(self, session_id: Optional[str]) -> tuple[AgentSession, bool]:
        """返回 (session, created)。session_id 为空或失效则新建。"""
        with self._lock:
            self._cleanup_locked()  # 清理不只挂在 create 上，长期无新会话也能回收
        self._cleanup_disk()  # 锁外 + 节流：磁盘 GC 跟内存字典无关，不需要占着全局锁
        if session_id:
            existing = self.get(session_id)
            if existing:
                return existing, False
        return self.create(), True

    def save(self, session: AgentSession) -> None:
        """turn 结束落盘（API 层调用）。锁/monitor/pending 不序列化。"""
        if not persist_enabled():
            return
        if not _SID_RE.match(session.session_id):
            return
        try:
            _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "session_id": session.session_id,
                "messages": session.messages,
                "allowed_tools": (sorted(session.allowed_tools)
                                  if session.allowed_tools is not None else None),
                "last_page_sig": session.last_page_sig,
                "turn_start_idx": session.turn_start_idx,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            }
            tmp = _PERSIST_DIR / f"{session.session_id}.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, default=str),
                           encoding="utf-8")
            tmp.replace(_PERSIST_DIR / f"{session.session_id}.json")
        except Exception as e:  # noqa: BLE001 — 持久化失败不可影响对话
            logger.warning(f"session 落盘失败 {session.session_id}: {e}")

    def _load_from_disk(self, session_id: str) -> Optional[AgentSession]:
        if not persist_enabled() or not session_id or not _SID_RE.match(session_id):
            return None
        path = _PERSIST_DIR / f"{session_id}.json"
        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            sess = AgentSession(
                session_id=session_id,
                messages=data.get("messages") or [],
                allowed_tools=(set(data["allowed_tools"])
                               if data.get("allowed_tools") is not None else None),
                last_page_sig=data.get("last_page_sig"),
                turn_start_idx=int(data.get("turn_start_idx") or 0),
                created_at=float(data.get("created_at") or time.time()),
            )
            logger.info(f"session 从盘复活 {session_id}（{len(sess.messages)} 条消息）")
            return sess
        except Exception as e:  # noqa: BLE001
            logger.warning(f"session 读盘失败 {session_id}: {e}")
            return None

    def _cleanup_locked(self) -> None:
        """内存驱逐——必须在 self._lock 内跑（保护 self._sessions 字典），本身很
        便宜（只是遍历当前活跃 session 数）。跳过 run_lock 被持有的 session：
        它的持有区间完整覆盖"_loop() 在跑 + 落盘中"这整个窗口（见 api/routes/
        agent.py 的 _drain 线程 finally 块：先 STORE.save() 再 release()），
        不检查这个会导致长耗时 turn（如 run_alpha_lab 多轮迭代，中途不调
        touch()）被误驱逐——后续同 session_id 请求会从磁盘复活出一个全新、
        未锁定 run_lock 的对象，跟仍在跑的旧对象并发写，后写覆盖先写丢对话。
        """
        now = time.time()
        stale = [sid for sid, s in self._sessions.items()
                 if now - s.updated_at > self._max_age and not s.run_lock.locked()]
        for sid in stale:
            del self._sessions[sid]

    def _cleanup_disk(self) -> None:
        """磁盘落盘文件 GC——跟 self._sessions 内存字典无关，不需要 self._lock
        保护；节流到最多每 _DISK_CLEANUP_INTERVAL_S 跑一次，避免每条聊天消息
        都付一次全目录 glob + 逐文件 stat() 的代价（旧版在 _cleanup_locked 里
        无节流地跑，且占着全局锁，序列化了所有并发会话请求）。"""
        now = time.time()
        if now - self._last_disk_cleanup_at < _DISK_CLEANUP_INTERVAL_S:
            return
        self._last_disk_cleanup_at = now
        if not persist_enabled():
            return
        try:
            cutoff = now - _PERSIST_MAX_AGE_DAYS * 86400
            for f in _PERSIST_DIR.glob("mb_*.json"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — 文件 GC 失败无关紧要
            pass


STORE = SessionStore()
