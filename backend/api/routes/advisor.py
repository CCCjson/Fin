"""
AI 投资顾问 API
"""
import asyncio
import queue
import threading
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from advisor_engine.service import AdvisorService
from llm_config import get_best_model

router = APIRouter(prefix="/advisor", tags=["AI投资顾问"])

# 全局服务实例（会话存储在内存中）
_service = AdvisorService()


class ChatRequest(BaseModel):
    """对话请求"""
    symbol: str = Field(..., description="股票代码，如 600519.SH")
    session_id: Optional[str] = Field(None, description="会话ID，首次不填自动创建")
    message: Optional[str] = Field(None, description="追问内容，首次分析不填")
    enable_web_search: bool = Field(False, description="是否联网搜索新闻")
    model: str = Field(default_factory=get_best_model, description="模型，默认最强档（投资建议）")


@router.post("/chat", summary="AI投资顾问对话（流式）")
async def advisor_chat(request: ChatRequest):
    """
    AI 投资顾问流式对话（NDJSON）。

    首次分析：只传 symbol，自动收集数据并分析。
    追问：传 symbol + session_id + message。

    事件类型:
    - session_created: 会话已创建
    - collecting: 正在收集数据
    - start: 开始 AI 分析
    - chunk: Markdown 内容片段
    - done: 分析完成
    - error: 出错
    """

    async def _streaming():
        try:
            # 确定 session_id
            sid = request.session_id
            if not sid:
                sid = _service.create_session(request.symbol)
                import json
                yield json.dumps(
                    {"event": "session_created", "session_id": sid},
                    ensure_ascii=False,
                ) + "\n"
                await asyncio.sleep(0)

            # chat_stream 是同步生成器，用 thread + queue 桥接到 async
            sync_gen = _service.chat_stream(
                session_id=sid,
                user_message=request.message,
                enable_web_search=request.enable_web_search,
                model=request.model,
            )

            chunk_queue: queue.Queue = queue.Queue()
            _SENTINEL = object()

            def _drain():
                try:
                    for item in sync_gen:
                        chunk_queue.put(item)
                except Exception as exc:
                    chunk_queue.put(exc)
                finally:
                    chunk_queue.put(_SENTINEL)

            thread = threading.Thread(target=_drain, daemon=True)
            thread.start()

            while True:
                try:
                    item = chunk_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue

                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item

                yield item
                await asyncio.sleep(0)

        except Exception as e:
            import json
            logger.error(f"advisor chat 流式异常: {e}")
            yield json.dumps(
                {"event": "error", "message": str(e)},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
