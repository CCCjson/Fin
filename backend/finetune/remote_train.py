"""
SSH 桥接模块 — 远程服务器训练 (NVIDIA GPU)

基于统一的 remote.executor 执行远程 Fine-Tune 训练脚本。
"""
import os
from pathlib import Path
from typing import Generator, Optional

from dotenv import load_dotenv
from loguru import logger

from remote.ssh_bridge import connect, exec_command
from remote import executor

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ==================== 配置 ====================

TASK_ID = "finetune"


def _get_finetune_config() -> dict:
    return {
        "remote_dir": os.getenv("FINETUNE_REMOTE_DIR", "/home/deepoptica/server_finetune"),
        "conda_env": os.getenv("FINETUNE_CONDA_ENV", "finetune"),
    }


# ==================== 远程数据统计 ====================


def get_remote_data_stats() -> dict:
    """通过 SSH 获取服务器上的训练数据统计"""
    cfg = _get_finetune_config()
    stats = {"train": 0, "valid": 0, "test": 0, "has_data": False}

    try:
        data_dir = f"{cfg['remote_dir']}/data/merged"
        for split in ("train", "valid", "test"):
            path = f"{data_dir}/{split}.jsonl"
            cmd = f"wc -l < {path} 2>/dev/null || echo 0"
            count = exec_command(cmd)
            stats[split] = int(count) if count.isdigit() else 0

        stats["has_data"] = stats["train"] > 0
    except Exception as e:
        logger.error(f"获取远程数据统计失败: {e}")

    return stats


# ==================== 流式训练 ====================


def run_streaming(
    iters: Optional[int] = None,
    learning_rate: Optional[float] = None,
    batch_size: Optional[int] = None,
    resume: bool = False,
) -> Generator[str, None, None]:
    """
    通过统一 executor 远程执行训练，逐行读取 stdout NDJSON 事件。
    """
    cfg = _get_finetune_config()

    args = {}
    if iters is not None and iters > 0:
        args["--iters"] = str(iters)
    if learning_rate is not None:
        args["--lr"] = str(learning_rate)
    if batch_size is not None:
        args["--batch_size"] = str(batch_size)
    if resume:
        args["--resume"] = ""

    yield from executor.run_streaming(
        task_id=TASK_ID,
        remote_dir=cfg["remote_dir"],
        conda_env=cfg["conda_env"],
        script="train.py",
        args=args if args else None,
    )


# ==================== 控制 ====================


def stop_training() -> bool:
    """终止远程训练"""
    return executor.stop(TASK_ID)


def is_training() -> bool:
    """是否有远程训练正在进行"""
    return executor.is_running(TASK_ID)
