"""
统一网络层 net —— 国内快代理韧性 + 国外 Clash 自适应。

国内数据源（东财/akshare）：
    from net import domestic_get, domestic_json, domestic_akshare, make_domestic_session, ProxyManager

⛔ 铁律：国内抓取失败只换快代理 IP 重试，**任何场景禁止降级本地直连**。
配了快代理却取不到 IP（额度耗尽）→ 抛 ProxyExhaustedError，一个请求都不发。
唯一合法直连：`.env` 压根没配快代理（那时直连是唯一选项，不是降级）。
国外出网（OpenAI/websearch）：
    from net import clash_alive, resolve_proxy, make_httpx_client, apply_proxy_env
"""
from net.bounded import (
    MAX_FILE_BYTES,
    MAX_RESPONSE_BYTES,
    Capped,
    bounded_get,
)
from net.clash import clash_alive, get_proxy_mode, reset_probe, resolve_proxy
from net.domestic import (
    ProxyExhaustedError,
    ProxyQuotaExhaustedError,
    ProxyRetriesExhaustedError,
    domestic_akshare,
    domestic_bounded_get,
    domestic_get,
    domestic_json,
    domestic_rotate,
    get_proxy_manager,
)
from net.env import apply_proxy_env, proxy_env
from net.proxy_manager import ProxyInfo, ProxyManager
from net.session import make_domestic_session, make_httpx_client

__all__ = [
    "clash_alive", "reset_probe", "resolve_proxy", "get_proxy_mode",
    "apply_proxy_env", "proxy_env",
    "make_domestic_session", "make_httpx_client",
    "ProxyManager", "ProxyInfo",
    "domestic_get", "domestic_json", "domestic_akshare", "get_proxy_manager",
    "domestic_bounded_get", "domestic_rotate",
    "ProxyExhaustedError", "ProxyQuotaExhaustedError", "ProxyRetriesExhaustedError",
    "bounded_get", "Capped", "MAX_RESPONSE_BYTES", "MAX_FILE_BYTES",
]
