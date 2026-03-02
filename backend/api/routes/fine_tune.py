"""
Fine-Tune API — 远程 GPU 训练可视化与流式进度（支持断线重连）
"""
import asyncio
import json
import queue
import threading
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/fine-tune", tags=["Fine-Tune"])


# ==================== 事件缓存（支持刷新重连）====================

_event_history: List[str] = []
_event_lock = threading.Lock()


def _clear_history():
    global _event_history
    with _event_lock:
        _event_history = []


def _append_event(event_str: str):
    with _event_lock:
        _event_history.append(event_str)


def _get_history() -> List[str]:
    with _event_lock:
        return list(_event_history)


# ==================== 请求模型 ====================

class StartRequest(BaseModel):
    mode: str = Field("remote", description="训练模式: remote (SSH 远程 GPU)")
    iters: int = Field(5000, description="训练迭代次数", ge=10, le=50000)
    learning_rate: float = Field(2e-4, description="学习率", gt=0, le=0.01)
    batch_size: int = Field(1, description="Batch size", ge=1, le=32)
    num_tests: int = Field(20, description="评估测试数量", ge=1, le=50)
    skip_evaluate: bool = Field(False, description="跳过评估阶段")
    skip_deploy: bool = Field(False, description="跳过部署阶段")


# ==================== 流式端点 ====================

@router.post("/start", summary="启动 Fine-Tune Pipeline（流式 NDJSON）")
async def start_fine_tune(request: StartRequest):
    """
    启动远程 Fine-Tuning Pipeline。

    返回 NDJSON 流式事件：
    - pipeline_start, stage_start, stage_complete
    - train_start, train_step, val_step, log, train_complete
    - pipeline_complete, pipeline_error
    """
    from finetune.remote_train import is_training as remote_is_training
    if remote_is_training():
        raise HTTPException(status_code=409, detail="远程训练正在进行中")

    # 清空历史，开始新训练
    _clear_history()

    async def _streaming():
        try:
            from finetune.pipeline import run_pipeline_streaming

            logger.info(f"Fine-Tune 启动: iters={request.iters}, lr={request.learning_rate}, bs={request.batch_size}")

            sync_gen = run_pipeline_streaming(
                iters=request.iters,
                learning_rate=request.learning_rate,
                batch_size=request.batch_size,
                num_tests=request.num_tests,
                skip_fuse=request.skip_deploy,
                mode=request.mode,
            )

            # 同步生成器 → 异步流（thread + queue 桥接）
            chunk_queue: queue.Queue = queue.Queue()
            _SENTINEL = object()

            def _drain():
                try:
                    for item in sync_gen:
                        _append_event(item)  # 缓存每个事件
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
            logger.error(f"Fine-Tune 流式异常: {e}")
            err_event = json.dumps(
                {"event": "pipeline_error", "stage": "unknown", "error": str(e)},
                ensure_ascii=False,
            ) + "\n"
            _append_event(err_event)
            yield err_event

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 断线重连 ====================

@router.get("/reconnect", summary="断线重连（获取缓存的事件）")
async def reconnect():
    """
    前端刷新后调用此接口，获取所有已缓存的训练事件。
    先回放历史，如果训练仍在进行则继续推送新事件直到结束。
    """
    history = _get_history()
    if not history:
        raise HTTPException(status_code=404, detail="没有正在进行或最近完成的训练")

    async def _replay_and_follow():
        # 阶段 1：回放已缓存的事件
        cursor = len(history)
        for event_str in history:
            yield event_str
            await asyncio.sleep(0)

        # 阶段 2：如果训练还在跑，继续跟踪新事件
        idle_count = 0
        while True:
            current = _get_history()
            if len(current) > cursor:
                for event_str in current[cursor:]:
                    yield event_str
                    await asyncio.sleep(0)
                cursor = len(current)
                idle_count = 0
            else:
                from finetune.remote_train import is_training as remote_is_training
                if not remote_is_training():
                    idle_count += 1
                    if idle_count > 20:  # 连续 1 秒无新事件且训练已停止 → 结束
                        break
                await asyncio.sleep(0.05)

    return StreamingResponse(
        _replay_and_follow(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== REST 端点 ====================

@router.post("/stop", summary="终止训练")
async def stop_fine_tune():
    """终止远程训练"""
    from finetune.remote_train import stop_training
    stopped = stop_training()
    return {"stopped": stopped}


@router.get("/status", summary="训练状态")
async def get_status():
    """检查是否有远程训练正在进行"""
    from finetune.remote_train import is_training as remote_is_training
    running = remote_is_training()

    return {
        "running": running,
        "mode": "remote" if running else None,
        "has_history": len(_event_history) > 0,
    }


@router.get("/data-stats", summary="训练数据统计")
async def get_data_stats():
    """获取远程服务器上的 train/valid/test 数据条数"""
    try:
        from finetune.remote_train import get_remote_data_stats
        return get_remote_data_stats()
    except Exception as e:
        logger.error(f"获取远程数据统计失败: {e}")
        return {"train": 0, "valid": 0, "test": 0, "has_data": False, "error": str(e)}
