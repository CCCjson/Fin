"""
MoneyBill multi-agent API —— 全局聊天主入口（流式 NDJSON）。

桥接模式照搬 advisor.py：同步 Generator(orchestrator) + thread/queue → async StreamingResponse。
"""
import asyncio
import json
import queue
import threading
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from agents.orchestrator import MonitorOrchestrator
from agents.context import STORE

router = APIRouter(prefix="/agent", tags=["MoneyBill"])

_orchestrator = MonitorOrchestrator()


class ConfirmPayload(BaseModel):
    tool_call_id: str
    approved: bool


class PageContext(BaseModel):
    page: str
    entities: dict = Field(default_factory=dict)


class AgentChatRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="会话ID，不填自动创建")
    message: Optional[str] = Field(None, description="用户输入；二次确认回传时可不填")
    model: Optional[str] = Field(None, description="覆盖模型，默认 best 档")
    confirm: Optional[ConfirmPayload] = Field(None, description="下单二次确认回传")
    page_context: Optional[PageContext] = Field(None, description="当前页面上下文")


def _ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


@router.post("/chat", summary="MoneyBill 对话（流式）")
async def agent_chat(request: AgentChatRequest):
    async def _streaming():
        try:
            session, created = STORE.get_or_create(request.session_id)
            if created:
                yield _ndjson({"event": "session_created",
                               "session_id": session.session_id})
                await asyncio.sleep(0)

            # 选择同步生成器：确认回传 vs 普通对话
            if request.confirm is not None:
                sync_gen = _orchestrator.resume_with_confirmation(
                    session, request.confirm.approved, model=request.model)
            else:
                if not request.message:
                    yield _ndjson({"event": "error", "message": "message 不能为空"})
                    return
                pc = request.page_context.model_dump() if request.page_context else None
                sync_gen = _orchestrator.run_stream(
                    session, request.message, model=request.model, page_context=pc)

            # thread + queue 桥接到 async
            chunk_queue: queue.Queue = queue.Queue()
            _SENTINEL = object()

            def _drain():
                try:
                    for item in sync_gen:
                        chunk_queue.put(item)
                except Exception as exc:  # noqa: BLE001
                    chunk_queue.put(exc)
                finally:
                    chunk_queue.put(_SENTINEL)

            thread = threading.Thread(target=_drain, daemon=True)
            thread.start()

            while True:
                try:
                    item = chunk_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.03)
                    continue
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
                await asyncio.sleep(0)

        except Exception as e:  # noqa: BLE001
            logger.error(f"MoneyBill chat 流式异常: {e}")
            yield _ndjson({"event": "error", "message": str(e)})

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/usage", summary="累计 token 用量与成本（美元）")
async def get_usage():
    from agents.usage import USAGE
    return USAGE.snapshot()


@router.get("/session/{session_id}", summary="拉取会话历史")
async def get_session(session_id: str):
    session = STORE.get(session_id)
    if not session:
        return {"found": False}
    # 只回前端要展示的对话消息（过滤掉 system / tool）
    msgs = [m for m in session.messages if m.get("role") in ("user", "assistant")]
    return {"found": True, "session_id": session_id, "messages": msgs}
