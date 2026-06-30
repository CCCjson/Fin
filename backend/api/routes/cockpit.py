"""
决策驾驶舱 API — 整合五维 → 综合买卖建议 + LLM 文字总结
"""
import asyncio
import json
import os
import queue
import threading

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from openai import OpenAI

from cockpit_engine.aggregator import CockpitAggregator
from cockpit_engine.prompt_builder import SYSTEM_PROMPT, build_summary_prompt
from llm_config import get_best_model, normalize_chat_params

router = APIRouter(prefix="/cockpit", tags=["决策驾驶舱"])

_aggregator = CockpitAggregator()


@router.get("/{symbol}", summary="决策驾驶舱聚合（五维分+综合建议）")
async def get_cockpit(symbol: str):
    """返回结构化聚合 + composite + 各维度分（aggregator 较重，放线程池）"""
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, _aggregator.aggregate, symbol)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"决策驾驶舱聚合失败 {symbol}: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


@router.post("/{symbol}/summary", summary="决策驾驶舱 LLM 文字总结（流式）")
async def cockpit_summary(symbol: str):
    """
    先聚合（线程池），再用最强档模型流式生成文字解读（NDJSON）。
    事件: cockpit(结构化数据) → chunk → done | error
    """
    async def _streaming():
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, _aggregator.aggregate, symbol)
            yield json.dumps({"event": "cockpit", "data": data}, ensure_ascii=False, default=str) + "\n"
            await asyncio.sleep(0)

            api_key = os.getenv("OPENAI_API_KEY", "")
            base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            if not api_key:
                yield json.dumps({"event": "error", "message": "OPENAI_API_KEY 未配置"}, ensure_ascii=False) + "\n"
                return
            from net_proxy import make_httpx_client
            client = OpenAI(api_key=api_key, base_url=base_url, http_client=make_httpx_client())

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_summary_prompt(data)},
            ]
            create_kwargs = normalize_chat_params(dict(
                model=get_best_model(),
                messages=messages,
                temperature=0.5,
                stream=True,
            ))

            chunk_queue: queue.Queue = queue.Queue()
            _SENTINEL = object()

            def _runner():
                try:
                    stream = client.chat.completions.create(**create_kwargs)
                    for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            chunk_queue.put(chunk.choices[0].delta.content)
                except Exception as exc:
                    chunk_queue.put(exc)
                finally:
                    chunk_queue.put(_SENTINEL)

            threading.Thread(target=_runner, daemon=True).start()

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
                yield json.dumps({"event": "chunk", "content": item}, ensure_ascii=False) + "\n"
                await asyncio.sleep(0)

            yield json.dumps({"event": "done"}, ensure_ascii=False) + "\n"

        except Exception as e:
            logger.error(f"决策驾驶舱总结流式异常 {symbol}: {e}")
            yield json.dumps({"event": "error", "message": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
