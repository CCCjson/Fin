"""
远程 GPU 命令执行器 — 统一流式 NDJSON 接口

支持多任务并行（通过 task_id 区分），每个任务独立的 SSH channel。
Fine-Tune / Prediction 等模块统一调用此接口。
"""
import json
import threading
import time
from typing import Dict, Generator, List, Optional

from loguru import logger

from remote.ssh_bridge import connect, get_ssh_config

# ==================== 任务管理 ====================

_tasks: Dict[str, dict] = {}  # task_id -> {"channel", "client", ...}
_tasks_lock = threading.Lock()


def _event(event: str, **data) -> str:
    """构造 NDJSON 事件行"""
    return json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"


def _build_remote_command(
    remote_dir: str,
    conda_env: str,
    script: str,
    args: Optional[Dict[str, str]] = None,
    ssh_user: str = "",
) -> str:
    """构建远程执行命令"""
    parts = [
        # 清除代理 + 强制无缓冲输出
        "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY;",
        "export PYTHONUNBUFFERED=1;",
        "export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True;",
    ]

    # conda 初始化
    if ssh_user:
        parts.append(f"source /home/{ssh_user}/miniconda3/etc/profile.d/conda.sh;")
    else:
        parts.append("source ~/miniconda3/etc/profile.d/conda.sh;")
    parts.append(f"conda activate {conda_env};")

    # cd 到工作目录 && 执行脚本
    parts.append(f"cd {remote_dir} && python -u {script}")

    # 追加参数
    if args:
        for key, value in args.items():
            if value is not None and value != "":
                parts.append(f"{key} {value}")
            elif value == "":
                # 布尔标志参数（如 --resume）
                parts.append(key)

    return " ".join(parts)


# ==================== 核心接口 ====================


def run_streaming(
    task_id: str,
    remote_dir: str,
    conda_env: str,
    script: str,
    args: Optional[Dict[str, str]] = None,
) -> Generator[str, None, None]:
    """
    通过 SSH 远程执行脚本，逐行 yield NDJSON 事件。

    Args:
        task_id: 任务标识（如 "finetune"、"prediction_600519"）
        remote_dir: 远程工作目录
        conda_env: conda 环境名
        script: 要执行的 Python 脚本
        args: 命令行参数 {"--iters": "5000", "--lr": "2e-4", ...}

    Yields:
        NDJSON 字符串（每行一个 JSON 事件）
    """
    cfg = get_ssh_config()

    remote_cmd = _build_remote_command(
        remote_dir=remote_dir,
        conda_env=conda_env,
        script=script,
        args=args,
        ssh_user=cfg["user"],
    )

    try:
        # 建立 SSH 连接
        yield _event("log", line=f"SSH 连接 {cfg['host']}...")
        client = connect()

        yield _event("log", line=f"远程命令: {remote_cmd}")

        # 通过 exec_command 执行（获取 channel 以支持终止）
        transport = client.get_transport()
        channel = transport.open_session()

        # 注册任务
        with _tasks_lock:
            _tasks[task_id] = {"channel": channel, "client": client}

        channel.exec_command(remote_cmd)

        # 逐行读取 stdout
        buffer = ""
        while True:
            if channel.exit_status_ready() and not channel.recv_ready():
                break

            if channel.recv_ready():
                chunk = channel.recv(4096).decode("utf-8", errors="replace")
                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    # 尝试解析为 NDJSON — 直接 yield
                    try:
                        json.loads(line)
                        yield line + "\n"
                    except json.JSONDecodeError:
                        # 非 JSON 输出作为日志
                        yield _event("log", line=line)
            else:
                time.sleep(0.1)

        # 处理剩余 buffer
        if buffer.strip():
            try:
                json.loads(buffer.strip())
                yield buffer.strip() + "\n"
            except json.JSONDecodeError:
                yield _event("log", line=buffer.strip())

        # 检查退出码
        exit_code = channel.recv_exit_status()
        if exit_code != 0:
            stderr_data = ""
            while channel.recv_stderr_ready():
                stderr_data += channel.recv_stderr(4096).decode("utf-8", errors="replace")
            if stderr_data:
                yield _event("log", line=f"[STDERR] {stderr_data.strip()}")
            yield _event("train_complete", success=False,
                         error=f"远程进程退出码: {exit_code}")

    except Exception as e:
        logger.error(f"SSH 远程执行异常 [{task_id}]: {e}")
        yield _event("train_complete", success=False, error=f"SSH 错误: {e}")
        yield _event("pipeline_error", stage="train", error=f"SSH 错误: {e}")

    finally:
        # 清理任务
        with _tasks_lock:
            task = _tasks.pop(task_id, None)
        if task and task.get("client"):
            try:
                task["client"].close()
            except Exception:
                pass


def stop(task_id: str) -> bool:
    """终止指定任务"""
    with _tasks_lock:
        task = _tasks.get(task_id)

    if not task:
        return False

    channel = task.get("channel")
    client = task.get("client")

    # 方法 1：发送 Ctrl+C
    if channel is not None:
        try:
            channel.send(b"\x03")
            logger.info(f"已向远程进程 [{task_id}] 发送 SIGINT")
            return True
        except Exception as e:
            logger.error(f"发送 SIGINT 失败 [{task_id}]: {e}")

    # 方法 2：通过新 SSH 连接 pkill
    try:
        kill_client = connect()
        try:
            # 根据 task_id 构建 kill 命令
            kill_cmd = f"pkill -f 'python.*{task_id}' || pkill -f 'python train.py' || true"
            kill_client.exec_command(kill_cmd)
            logger.info(f"已通过 pkill 终止远程训练 [{task_id}]")
            return True
        finally:
            kill_client.close()
    except Exception as e:
        logger.error(f"pkill 远程训练失败 [{task_id}]: {e}")

    return False


def is_running(task_id: str) -> bool:
    """检查指定任务是否在运行"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return False
    channel = task.get("channel")
    return channel is not None and not channel.exit_status_ready()


def get_running_tasks() -> List[str]:
    """获取所有正在运行的任务 ID"""
    with _tasks_lock:
        return [
            tid for tid, task in _tasks.items()
            if task.get("channel") and not task["channel"].exit_status_ready()
        ]
