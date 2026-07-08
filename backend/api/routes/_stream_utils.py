"""
流式端点通用工具：把阻塞式同步生成器桥接为 async 流。

背景：若在 async 端点里直接 `for chunk in sync_gen` 迭代同步生成器，
每次 next() 的阻塞式网络/IO 会占住 uvicorn 的单个事件循环线程，
导致整个后端所有请求卡死。此工具把生成器丢进 daemon 线程跑，
事件循环只做轻量轮询，绝不被阻塞。

该模式最早在 advisor.py / agent.py / prediction.py 内联实现，这里抽出复用。

2026-07-07 补：改为「调用即启动」——原实现是 async 生成器函数，drain 线程要
等 async 侧第一次 `__anext__` 才会启动。如果调用方想在生成器开始跑之前就
持有一把互斥锁（如日线更新的 409 防重复），锁获取和线程启动之间会有窗口；
客户端秒断连接时线程可能永远不会启动，锁也就永远不会被 `on_finish` 释放
（agent.py 会话锁曾踩过同一个坑）。改成普通函数后，调用 `bridge_sync_stream(...)`
的瞬间线程就已经在跑，返回值只是给事件循环消费的 async 生成器。
"""
import asyncio
import queue
import threading
from typing import AsyncIterator, Callable, Iterator, Optional

from loguru import logger

_SENTINEL = object()


def bridge_sync_stream(
    sync_gen: Iterator[str],
    on_finish: Optional[Callable[[], None]] = None,
) -> AsyncIterator[str]:
    """把阻塞式同步生成器桥接为 async 流。调用即启动 drain 线程（不等首次迭代）。

    - 生成器在 daemon 线程里跑，承担所有阻塞式网络/DB 工作；
    - 事件循环侧只 `get_nowait` + 短 sleep 轮询，其它 API 请求随时可被处理；
    - 生成器抛出的异常会透传到 async 侧重新 raise；
    - 结束时发送哨兵，正常收尾。

    Args:
        sync_gen: 产出字符串 chunk 的同步生成器（如 ndjson 行）。
        on_finish: drain 线程结束时（正常结束/异常/生成器被提前关闭）调用一次，
            无论成败都会执行。用于释放调用方持有的互斥锁等收尾动作。

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
            if on_finish is not None:
                try:
                    on_finish()
                except Exception as cb_exc:  # noqa: BLE001 — 收尾回调本身不应打断流
                    logger.warning(f"bridge_sync_stream on_finish 回调异常: {cb_exc}")

    threading.Thread(target=_drain, daemon=True).start()

    async def _consume() -> AsyncIterator[str]:
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

    return _consume()
