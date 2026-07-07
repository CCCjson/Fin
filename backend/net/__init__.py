"""
统一网络层 net —— 国内快代理韧性 + 国外 Clash 自适应。

国内数据源（东财/akshare）：
    from net import domestic_get, domestic_json, domestic_akshare, make_domestic_session, ProxyManager
国外出网（OpenAI/websearch）：
    from net import clash_alive, resolve_proxy, make_httpx_client, apply_proxy_env
"""
from net.clash import clash_alive, reset_probe, resolve_proxy, get_proxy_mode
from net.env import apply_proxy_env, proxy_env
from net.session import make_domestic_session, make_httpx_client
from net.proxy_manager import ProxyManager, ProxyInfo
from net.domestic import (
    domestic_get,
    domestic_json,
    domestic_akshare,
    get_proxy_manager,
)

__all__ = [
    "clash_alive", "reset_probe", "resolve_proxy", "get_proxy_mode",
    "apply_proxy_env", "proxy_env",
    "make_domestic_session", "make_httpx_client",
    "ProxyManager", "ProxyInfo",
    "domestic_get", "domestic_json", "domestic_akshare", "get_proxy_manager",
]
