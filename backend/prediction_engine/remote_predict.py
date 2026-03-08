"""
Prediction Engine 远程 GPU 训练桥接

基于统一的 remote.executor 执行远程 LSTM+XGBoost 训练脚本。
远程服务器上的 predict_train.py 输出 NDJSON 事件，格式与 Fine-Tune 一致。
"""
import os
from pathlib import Path
from typing import Generator, Optional

from dotenv import load_dotenv
from loguru import logger

from remote.ssh_bridge import exec_command
from remote import executor

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ==================== 配置 ====================

TASK_ID_PREFIX = "prediction"


def _get_prediction_config() -> dict:
    return {
        "remote_dir": os.getenv("PREDICTION_REMOTE_DIR", "/home/deepoptica/server_prediction"),
        "conda_env": os.getenv("PREDICTION_CONDA_ENV", "finetune"),
    }


def _task_id(symbol: str) -> str:
    """每个股票的训练任务有独立 task_id"""
    return f"{TASK_ID_PREFIX}_{symbol.replace('.', '_')}"


# ==================== 远程训练 ====================


def run_training(
    symbol: str,
    period: str = "2y",
    forward_days: int = 5,
    epochs: int = 100,
    hidden_dim: int = 256,
    batch_size: int = 64,
) -> Generator[str, None, None]:
    """
    远程执行 LSTM+XGBoost 训练，流式返回 NDJSON 事件。

    远程脚本 predict_train.py 需要接受以下参数：
        --symbol, --period, --forward_days, --epochs, --hidden_dim, --batch_size

    输出事件格式：
        {"event": "progress", "stage": "...", "progress": 0.xx, "message": "..."}
        {"event": "complete", "progress": 1.0, "result": {...}}
        {"event": "error", "message": "..."}
    """
    cfg = _get_prediction_config()
    tid = _task_id(symbol)

    args = {
        "--symbol": symbol,
        "--period": period,
        "--forward_days": str(forward_days),
        "--epochs": str(epochs),
        "--hidden_dim": str(hidden_dim),
        "--batch_size": str(batch_size),
    }

    yield from executor.run_streaming(
        task_id=tid,
        remote_dir=cfg["remote_dir"],
        conda_env=cfg["conda_env"],
        script="predict_train.py",
        args=args,
    )


# ==================== 控制 ====================


def stop_training(symbol: str) -> bool:
    """终止指定股票的训练"""
    return executor.stop(_task_id(symbol))


def is_training(symbol: Optional[str] = None) -> bool:
    """检查是否有预测模型训练在进行"""
    if symbol:
        return executor.is_running(_task_id(symbol))
    # 检查所有 prediction 任务
    running = executor.get_running_tasks()
    return any(t.startswith(TASK_ID_PREFIX) for t in running)


# ==================== 远程模型查询 ====================


def get_remote_models() -> list:
    """查询远程服务器上已有的训练好的模型"""
    cfg = _get_prediction_config()
    try:
        cmd = f"ls -d {cfg['remote_dir']}/saved_models/*/ 2>/dev/null | xargs -I{{}} basename {{}}"
        output = exec_command(cmd)
        if not output:
            return []
        return [s.replace("_", ".") for s in output.strip().split("\n") if s]
    except Exception as e:
        logger.error(f"查询远程模型失败: {e}")
        return []
