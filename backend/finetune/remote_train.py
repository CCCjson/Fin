"""
SSH 桥接模块 — 远程服务器训练 (NVIDIA GPU)

通过 paramiko SSH 连接远程服务器，执行 train.py，
逐行读取 stdout 的 NDJSON 事件并 yield，
与本地 train.run_streaming() 接口完全一致。
"""
import json
import os
import signal
import threading
from pathlib import Path
from typing import Generator, Optional

from dotenv import load_dotenv
from loguru import logger

# 加载 .env
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ==================== 配置 ====================


def _get_ssh_config() -> dict:
    """从环境变量读取 SSH 配置，支持主/备双线路（公司内网 + 家里穿透）"""
    return {
        "host": os.getenv("FINETUNE_SSH_HOST", ""),
        "user": os.getenv("FINETUNE_SSH_USER", ""),
        "port": int(os.getenv("FINETUNE_SSH_PORT", "22")),
        "key_path": os.path.expanduser(os.getenv("FINETUNE_SSH_KEY", "~/.ssh/id_rsa")),
        "remote_dir": os.getenv("FINETUNE_REMOTE_DIR", "/home/jason/server_finetune"),
        # 备用线路（家里内网穿透）
        "host_alt": os.getenv("FINETUNE_SSH_HOST_ALT", ""),
        "port_alt": int(os.getenv("FINETUNE_SSH_PORT_ALT", "22")),
    }


def _event(event: str, **data) -> str:
    """构造 NDJSON 事件行"""
    return json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"


# ==================== SSH 连接 ====================

_ssh_client = None
_ssh_channel = None
_training_lock = threading.Lock()


def _connect_ssh():
    """建立 SSH 连接（自动探测：先试主线路，不通则试备用线路）"""
    import paramiko
    import socket

    cfg = _get_ssh_config()
    if not cfg["host"] or not cfg["user"]:
        raise ValueError("SSH 配置缺失，请在 .env 中设置 FINETUNE_SSH_HOST 和 FINETUNE_SSH_USER")

    password = os.getenv("FINETUNE_SSH_PASSWORD", "")

    # 构建候选线路：主线路 + 备用线路
    candidates = [(cfg["host"], cfg["port"])]
    if cfg["host_alt"]:
        candidates.append((cfg["host_alt"], cfg["port_alt"]))

    last_error = None
    for host, port in candidates:
        try:
            # 先快速测试端口是否可达（3秒超时）
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:
                logger.info(f"SSH {host}:{port} 不可达，跳过")
                continue

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": host,
                "port": port,
                "username": cfg["user"],
                "timeout": 10,
                "allow_agent": True,
                "look_for_keys": True,
            }
            if password:
                connect_kwargs["password"] = password

            logger.info(f"SSH 连接 {cfg['user']}@{host}:{port}")
            client.connect(**connect_kwargs)
            logger.info(f"SSH 连接成功: {host}:{port}")
            return client

        except Exception as e:
            last_error = e
            logger.warning(f"SSH {host}:{port} 连接失败: {e}")
            continue

    raise ConnectionError(f"所有 SSH 线路均连接失败: {last_error}")


# ==================== 远程数据统计 ====================


def get_remote_data_stats() -> dict:
    """通过 SSH 获取服务器上的训练数据统计"""
    cfg = _get_ssh_config()
    stats = {"train": 0, "valid": 0, "test": 0, "has_data": False}

    try:
        client = _connect_ssh()
        try:
            data_dir = f"{cfg['remote_dir']}/data/merged"
            for split in ("train", "valid", "test"):
                path = f"{data_dir}/{split}.jsonl"
                cmd = f"wc -l < {path} 2>/dev/null || echo 0"
                _, stdout, _ = client.exec_command(cmd)
                count = stdout.read().decode().strip()
                stats[split] = int(count) if count.isdigit() else 0

            stats["has_data"] = stats["train"] > 0
        finally:
            client.close()
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
    通过 SSH 远程执行训练，逐行读取 stdout NDJSON 事件。

    接口与 train.run_streaming() 一致，可直接替换。
    """
    global _ssh_client, _ssh_channel

    cfg = _get_ssh_config()

    # 构建远程命令
    remote_dir = cfg["remote_dir"]
    remote_user = cfg["user"]
    cmd_parts = [
        # 清除代理 + 初始化 conda（非交互式 shell 不会加载 .bashrc）
        "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY;",
        "export PYTHONUNBUFFERED=1;",
        "export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True;",
        f"source /home/{remote_user}/miniconda3/etc/profile.d/conda.sh;",
        f"conda activate finetune;",
        f"cd {remote_dir}",
        "&&",
        "python", "-u", "train.py",
    ]
    if iters is not None and iters > 0:
        cmd_parts.extend(["--iters", str(iters)])
    if learning_rate is not None:
        cmd_parts.extend(["--lr", str(learning_rate)])
    if batch_size is not None:
        cmd_parts.extend(["--batch_size", str(batch_size)])
    if resume:
        cmd_parts.append("--resume")

    remote_cmd = " ".join(cmd_parts)

    with _training_lock:
        try:
            # 建立 SSH 连接
            yield _event("log", line=f"SSH 连接 {cfg['host']}...")
            client = _connect_ssh()
            _ssh_client = client

            yield _event("log", line=f"远程命令: {remote_cmd}")

            # 通过 exec_command 执行（获取 channel 以支持终止）
            transport = client.get_transport()
            channel = transport.open_session()
            _ssh_channel = channel

            channel.exec_command(remote_cmd)

            # 逐行读取 stdout
            buffer = ""
            while True:
                # 检查 channel 是否关闭
                if channel.exit_status_ready() and not channel.recv_ready():
                    break

                if channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    buffer += chunk

                    # 按行分割
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
                    # 没有数据，短暂等待
                    import time
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
                # 读取 stderr
                stderr_data = ""
                while channel.recv_stderr_ready():
                    stderr_data += channel.recv_stderr(4096).decode("utf-8", errors="replace")
                if stderr_data:
                    yield _event("log", line=f"[STDERR] {stderr_data.strip()}")

                # 如果没收到 train_complete，补发一个
                yield _event("train_complete", success=False,
                             error=f"远程进程退出码: {exit_code}")

        except Exception as e:
            logger.error(f"SSH 远程训练异常: {e}")
            yield _event("train_complete", success=False, error=f"SSH 错误: {e}")
            yield _event("pipeline_error", stage="train", error=f"SSH 错误: {e}")

        finally:
            _ssh_channel = None
            if _ssh_client:
                try:
                    _ssh_client.close()
                except Exception:
                    pass
                _ssh_client = None


# ==================== 控制 ====================


def stop_training() -> bool:
    """通过 SSH 终止远程训练"""
    global _ssh_client, _ssh_channel

    if _ssh_channel is not None:
        try:
            # 发送 Ctrl+C (SIGINT)
            _ssh_channel.send(b"\x03")
            logger.info("已向远程进程发送 SIGINT")
            return True
        except Exception as e:
            logger.error(f"终止远程训练失败: {e}")

    # 备选：通过新 SSH 连接 kill
    if _ssh_client is not None:
        try:
            cfg = _get_ssh_config()
            client = _connect_ssh()
            try:
                kill_cmd = "pkill -f 'python train.py' || true"
                client.exec_command(kill_cmd)
                logger.info("已通过 pkill 终止远程训练")
                return True
            finally:
                client.close()
        except Exception as e:
            logger.error(f"pkill 远程训练失败: {e}")

    return False


def is_training() -> bool:
    """是否有远程训练正在进行"""
    return _ssh_channel is not None and not _ssh_channel.exit_status_ready()
