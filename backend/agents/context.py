"""
会话状态 — AgentSession + 内存会话池（参考 advisor_engine/service.py 的 ConversationSession）。
"""
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


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
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionStore:
    """内存会话池，带过期清理（参考 AdvisorService）。"""

    def __init__(self, max_age: int = 3600) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.Lock()
        self._max_age = max_age

    def create(self) -> AgentSession:
        sid = f"mb_{uuid.uuid4().hex[:12]}"
        sess = AgentSession(session_id=sid)
        with self._lock:
            self._cleanup_locked()
            self._sessions[sid] = sess
        return sess

    def get(self, session_id: str) -> Optional[AgentSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create(self, session_id: Optional[str]) -> tuple[AgentSession, bool]:
        """返回 (session, created)。session_id 为空或失效则新建。"""
        if session_id:
            existing = self.get(session_id)
            if existing:
                return existing, False
        return self.create(), True

    def _cleanup_locked(self) -> None:
        now = time.time()
        stale = [sid for sid, s in self._sessions.items()
                 if now - s.updated_at > self._max_age]
        for sid in stale:
            del self._sessions[sid]


STORE = SessionStore()
