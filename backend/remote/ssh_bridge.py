"""
SSH 桥接层 — 统一远程 GPU 服务器连接管理

支持双线路自动探测（主线路 + 备用线路）、连接复用、自动重连。
三个模块（Fine-Tune / Prediction / Alpha Lab）共享同一套 SSH 配置。
"""
import os
import socket
import threading
from pathlib import Path
from typing import Optional

import paramiko
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ==================== 配置 ====================

_lock = threading.Lock()
_cached_client: Optional[paramiko.SSHClient] = None


def get_ssh_config() -> dict:
    """从环境变量读取统一 SSH 配置"""
    return {
        "host": os.getenv("REMOTE_GPU_SSH_HOST", ""),
        "port": int(os.getenv("REMOTE_GPU_SSH_PORT", "22")),
        "host_alt": os.getenv("REMOTE_GPU_SSH_HOST_ALT", ""),
        "port_alt": int(os.getenv("REMOTE_GPU_SSH_PORT_ALT", "22")),
        "user": os.getenv("REMOTE_GPU_SSH_USER", ""),
        "password": os.getenv("REMOTE_GPU_SSH_PASSWORD", ""),
        "key_path": os.path.expanduser(os.getenv("REMOTE_GPU_SSH_KEY", "~/.ssh/id_rsa")),
    }


# ==================== 连接管理 ====================


def connect(timeout: int = 10) -> paramiko.SSHClient:
    """
    建立 SSH 连接（自动探测：先试主线路，不通则试备用线路）。

    每次调用返回一个新连接，调用方负责关闭。
    """
    cfg = get_ssh_config()
    if not cfg["host"] or not cfg["user"]:
        raise ValueError("SSH 配置缺失，请在 .env 中设置 REMOTE_GPU_SSH_HOST 和 REMOTE_GPU_SSH_USER")

    # 构建候选线路
    candidates = [(cfg["host"], cfg["port"])]
    if cfg["host_alt"]:
        candidates.append((cfg["host_alt"], cfg["port_alt"]))

    last_error = None
    for host, port in candidates:
        try:
            # 快速测试端口是否可达
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
                "timeout": timeout,
                "allow_agent": True,
                "look_for_keys": True,
            }
            if cfg["password"]:
                connect_kwargs["password"] = cfg["password"]

            logger.info(f"SSH 连接 {cfg['user']}@{host}:{port}")
            client.connect(**connect_kwargs)
            logger.info(f"SSH 连接成功: {host}:{port}")
            return client

        except Exception as e:
            last_error = e
            logger.warning(f"SSH {host}:{port} 连接失败: {e}")
            continue

    raise ConnectionError(f"所有 SSH 线路均连接失败: {last_error}")


def exec_command(cmd: str, timeout: int = 30) -> str:
    """
    一次性执行远程命令并返回 stdout。

    用于简单查询（如获取数据统计）。连接用完即关。
    """
    client = connect()
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        output = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if err:
            logger.debug(f"SSH stderr: {err}")
        return output
    finally:
        client.close()
