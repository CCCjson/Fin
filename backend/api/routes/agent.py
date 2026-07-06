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
from fastapi.responses import JSONResponse, StreamingResponse
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
    path: Optional[str] = Field(None, description="前端路由路径，如 /watchlist（工具组按页预载用）")
    entities: dict = Field(default_factory=dict)
    visible_text: Optional[str] = Field(
        None, description="当前页面可见文本（文字版截图，供 LLM 理解指代）")


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
    session, created = STORE.get_or_create(request.session_id)

    # 同一 session 同时只允许一个 turn：并发会踩坏 messages/pending_tool_call（钱路状态）
    if not session.run_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"event": "error",
                     "message": "上一轮对话还在进行中，请等它结束或断开后重试"})
    session.cancel_event.clear()

    # 选择同步生成器：确认回传 vs 普通对话（生成器构造是惰性的，此处不真正执行）
    if request.confirm is not None:
        sync_gen = _orchestrator.resume_with_confirmation(
            session, request.confirm.approved,
            tool_call_id=request.confirm.tool_call_id, model=request.model)
    elif not request.message:
        # 空消息：此路不通，立即放锁返回。不能把这个 guard 留到流式生成器里——
        # 若客户端在响应体被迭代前就断开，那个生成器可能永不运行，锁就漏了。
        session.run_lock.release()
        return JSONResponse(
            status_code=400,
            content={"event": "error", "message": "message 不能为空"})
    else:
        pc = request.page_context.model_dump() if request.page_context else None
        sync_gen = _orchestrator.run_stream(
            session, request.message, model=request.model, page_context=pc)

    # thread + queue 桥接到 async。queue.Queue 无界，put() 永不阻塞——所以 _drain
    # 线程即便没有任何消费者也能把整轮跑完并走到 finally 放锁。
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
            try:
                STORE.save(session)  # turn 结束落盘（在放锁前，保证状态一致）
            finally:
                session.run_lock.release()

    # 关键修复：在路由函数体内同步启动 _drain，而不是等流式生成器被 ASGI 迭代才启动。
    # 客户端若在响应体开始被迭代前就断开（请求刚发出即取消），那个流式生成器的 body
    # 可能一次都不运行——旧写法把 thread.start()/放锁兜底都放在生成器体里，就会导致
    # run_lock 永久泄漏（该 session 之后永远 409、且被 _cleanup_locked 跳过不回收）。
    # 线程一旦 start，无论后续响应流是否被消费，它都独立跑到 finally 放锁。
    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()

    async def _streaming():
        # 只负责从 queue 消费并 yield，不再承担驱动线程生命周期的职责。
        try:
            if created:
                yield _ndjson({"event": "session_created",
                               "session_id": session.session_id})
                await asyncio.sleep(0)
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
        finally:
            # 客户端断开（本 async 生成器被取消）或正常结束都会走到这里：
            # 置协作取消位，orchestrator 在下一轮轮首会停止，不再白烧 token。
            # 放锁不在这里——由 _drain 线程 finally 独占负责，避免双重释放。
            session.cancel_event.set()

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
