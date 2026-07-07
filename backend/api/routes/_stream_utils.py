"""
流式端点通用工具：把阻塞式同步生成器桥接为 async 流。

背景：若在 async 端点里直接 `for chunk in sync_gen` 迭代同步生成器，
每次 next() 的阻塞式网络/IO 会占住 uvicorn 的单个事件循环线程，
导致整个后端所有请求卡死。此工具把生成器丢进 daemon 线程跑，
事件循环只做轻量轮询，绝不被阻塞。

该模式最早在 advisor.py / agent.py / prediction.py 内联实现，这里抽出复用。
"""
import asyncio
import queue
import threading
from typing import AsyncIterator, Iterator

_SENTINEL = object()


async def bridge_sync_stream(sync_gen: Iterator[str]) -> AsyncIterator[str]:
    """把阻塞式同步生成器桥接为 async 流。

    - 生成器在 daemon 线程里跑，承担所有阻塞式网络/DB 工作；
    - 事件循环侧只 `get_nowait` + 短 sleep 轮询，其它 API 请求随时可被处理；
    - 生成器抛出的异常会透传到 async 侧重新 raise；
    - 结束时发送哨兵，正常收尾。

    Args:
        sync_gen: 产出字符串 chunk 的同步生成器（如 ndjson 行）。

    Yields:
        生成器逐个产出的 chunk。
    """
    chunk_queue: "queue.Queue" = queue.Queue()

    def _drain() -> None:
        try:
            for item in sync_gen:
                chunk_queue.put(item)
        except Exception as exc:  # noqa: BLE001 — 透传给 async 侧重新 raise
            chunk_queue.put(exc)
        finally:
            chunk_queue.put(_SENTINEL)

    threading.Thread(target=_drain, daemon=True).start()

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
