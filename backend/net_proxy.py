"""
[兼容 shim] 全系统出网代理自适配 —— 实现已迁至 net/ 包。

保留本模块只为不破坏历史 import（api/main.py、knowledge_engine、各 llm_client 等）。
新代码请直接 `from net import ...`。
"""
from net.clash import clash_alive, reset_probe, resolve_proxy, get_proxy_mode  # noqa: F401
from net.env import apply_proxy_env  # noqa: F401
from net.session import make_httpx_client  # noqa: F401

__all__ = [
    "clash_alive", "reset_probe", "resolve_proxy", "get_proxy_mode",
    "apply_proxy_env", "make_httpx_client",
]
