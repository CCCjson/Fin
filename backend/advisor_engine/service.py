"""
AI 投资顾问 — 核心服务
会话管理 + OpenAI 流式调用
"""
import json
import os
import threading
import time
import uuid
from typing import Dict, Generator, List, Optional

from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI
from httpx import Timeout as HttpxTimeout

from advisor_engine.context_collector import AdvisorContextCollector
from advisor_engine.prompt_builder import AdvisorPromptBuilder

load_dotenv(override=True)


class ConversationSession:
    """单个对话会话"""

    def __init__(self, session_id: str, symbol: str):
        self.session_id = session_id
        self.symbol = symbol
        self.messages: List[Dict] = []  # OpenAI messages [{role, content}]
        self.context_data: Optional[Dict] = None
        self.created_at: float = time.time()

    def get_messages(self) -> List[Dict]:
        return list(self.messages)


class AdvisorService:
    """AI 投资顾问服务"""

    def __init__(self) -> None:
        self._sessions: Dict[str, ConversationSession] = {}
        self._lock = threading.Lock()

    def create_session(self, symbol: str) -> str:
        """创建新会话，返回 session_id"""
        with self._lock:
            self._cleanup_stale_sessions()
            session_id = f"adv_{uuid.uuid4().hex[:12]}"
            self._sessions[session_id] = ConversationSession(session_id, symbol)
            return session_id

    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def chat_stream(
        self,
        session_id: str,
        user_message: Optional[str],
        enable_web_search: bool = False,
        model: str = "gpt-4o",
    ) -> Generator[str, None, None]:
        """
        流式对话。

        首次分析: user_message 为空或 None → 自动收集数据并生成分析。
        追问: user_message 有内容 → 追加到会话并获取回复。

        Yields NDJSON 行。
        """
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            yield _ndjson({"event": "error", "message": "会话不存在"})
            return

        start_time = time.time()

        # ---------- 首次分析 ----------
        if session.context_data is None:
            yield _ndjson({"event": "collecting", "message": f"正在收集 {session.symbol} 的数据..."})

            try:
                collector = AdvisorContextCollector()
                ctx = collector.collect(session.symbol, enable_web_search=enable_web_search)
                session.context_data = ctx
            except Exception as e:
                logger.error(f"数据收集失败: {e}")
                yield _ndjson({"event": "error", "message": f"数据收集失败: {e}"})
                return

            yield _ndjson({"event": "collecting", "message": "数据收集完成，正在分析..."})

            # 构建 prompt
            builder = AdvisorPromptBuilder()
            system_prompt = builder.build_system_prompt()
            user_prompt = builder.build_first_analysis_prompt(ctx)

            session.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

        # ---------- 追问 ----------
        else:
            if not user_message:
                yield _ndjson({"event": "error", "message": "追问内容不能为空"})
                return
            session.messages.append({"role": "user", "content": user_message})

        # ---------- 调用 OpenAI 流式 ----------
        yield _ndjson({"event": "start", "session_id": session_id})

        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

        if not api_key:
            yield _ndjson({"event": "error", "message": "OPENAI_API_KEY 未配置"})
            return

        try:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=HttpxTimeout(connect=15.0, read=300.0, write=30.0, pool=30.0),
                max_retries=2,
            )

            stream_kwargs: Dict = dict(
                model=model,
                messages=session.get_messages(),
                temperature=0.5,
                stream=True,
            )
            try:
                stream = client.chat.completions.create(
                    **stream_kwargs,
                    stream_options={"include_usage": True},
                )
            except Exception:
                # 部分兼容端点不支持 stream_options，回退
                stream = client.chat.completions.create(**stream_kwargs)

            full_content = ""
            token_count = 0

            for chunk in stream:
                if chunk.usage:
                    token_count = chunk.usage.total_tokens

                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        full_content += delta.content
                        yield _ndjson({"event": "chunk", "content": delta.content})

            # 保存 assistant 回复
            session.messages.append({"role": "assistant", "content": full_content})

            elapsed = round(time.time() - start_time, 2)
            yield _ndjson({
                "event": "done",
                "session_id": session_id,
                "token_count": token_count,
                "generation_time": elapsed,
            })

        except Exception as e:
            logger.error(f"OpenAI 调用失败: {e}")
            yield _ndjson({"event": "error", "message": f"AI 分析失败: {e}"})

    def _cleanup_stale_sessions(self, max_age: int = 3600) -> None:
        """清理超时会话（调用方需持有 self._lock）"""
        now = time.time()
        stale = [sid for sid, s in self._sessions.items() if now - s.created_at > max_age]
        for sid in stale:
            del self._sessions[sid]


def _ndjson(obj: dict) -> str:
    """序列化为 NDJSON 行"""
    return json.dumps(obj, ensure_ascii=False) + "\n"
