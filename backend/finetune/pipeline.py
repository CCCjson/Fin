"""
一键编排入口 — 远程 SSH 训练 Pipeline

用法（API 调用，不直接运行）:
    由 api/routes/fine_tune.py 调用 run_pipeline_streaming()
"""
import json
import time
from typing import Generator, Optional

from loguru import logger


def _event(event: str, **data) -> str:
    return json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"


def run_pipeline_streaming(
    iters: Optional[int] = None,
    learning_rate: Optional[float] = None,
    batch_size: Optional[int] = None,
    num_tests: int = 20,
    skip_fuse: bool = False,
    mode: str = "remote",
    resume: bool = False,
) -> Generator[str, None, None]:
    """
    流式 Pipeline 生成器 — SSH 远程训练，yield NDJSON 事件。

    事件: pipeline_start / stage_start / stage_complete /
          train_start / train_step / val_step / log / train_complete /
          pipeline_complete / pipeline_error
    """
    stages = ["train"]

    yield _event("pipeline_start", stages=stages)

    for stage_index, stage in enumerate(stages):
        yield _event("stage_start", stage=stage, stage_index=stage_index, total_stages=len(stages))
        start_time = time.time()

        try:
            if stage == "train":
                from finetune.remote_train import run_streaming as remote_streaming
                for ev in remote_streaming(
                    iters=iters, learning_rate=learning_rate,
                    batch_size=batch_size, resume=resume,
                ):
                    yield ev
                    try:
                        parsed = json.loads(ev)
                        if parsed.get("event") == "train_complete" and not parsed.get("success"):
                            yield _event("pipeline_error", stage=stage, error=parsed.get("error", "远程训练失败"))
                            return
                    except json.JSONDecodeError:
                        pass

                elapsed = time.time() - start_time
                yield _event("stage_complete", stage=stage, data={"elapsed": round(elapsed, 1)})

        except Exception as e:
            logger.error(f"Pipeline 阶段 {stage} 异常: {e}")
            yield _event("pipeline_error", stage=stage, error=str(e))
            return

    yield _event("pipeline_complete")
