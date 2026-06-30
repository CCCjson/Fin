"""
全系统出网代理自适配 — 让 OpenAI / websearch 等出网请求不死绑 Clash。

策略（统一开关 env `HTTP_PROXY_MODE`，默认 auto）：
- auto    ：socket 探测 Clash(127.0.0.1:7897)，在线→走 Clash，离线→直连。
            一套配置同时适配国内(Clash)/新加坡(本地直翻)，无需改环境。
- direct/none：强制直连。
- 具体 URL：强制走该代理。

关键：构造的 httpx/requests client 一律 trust_env=False，不隐式继承系统
HTTPS_PROXY/ALL_PROXY（Clash 残留的死代理变量会害人）。
"""
import os
import socket
import threading

_CLASH_HOST, _CLASH_PORT = "127.0.0.1", 7897
_probe = {"alive": None}
_lock = threading.Lock()


def clash_alive() -> bool:
    """socket 探测 Clash 7897 是否在线（进程内缓存一次）。"""
    with _lock:
        if _probe["alive"] is None:
            try:
                s = socket.create_connection((_CLASH_HOST, _CLASH_PORT), timeout=0.5)
                s.close()
                _probe["alive"] = True
            except OSError:
                _probe["alive"] = False
        return _probe["alive"]


def reset_probe() -> None:
    """清掉探测缓存（Clash 状态变化后想立即重判时调用）。"""
    with _lock:
        _probe["alive"] = None


def get_proxy_mode() -> str:
    return os.getenv("HTTP_PROXY_MODE", "auto")


def resolve_proxy(force_direct: bool = False) -> str | None:
    """解析本次该用的代理 URL，None 表示直连。"""
    if force_direct:
        return None
    pref = (get_proxy_mode() or "auto").strip()
    low = pref.lower()
    if low in ("direct", "none", ""):
        return None
    if low == "auto":
        return f"http://{_CLASH_HOST}:{_CLASH_PORT}" if clash_alive() else None
    return pref


_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy")


def apply_proxy_env() -> str | None:
    """
    把进程的代理 env 同步到解析模式：**direct 模式（Clash 关）时清掉残留死代理变量**，
    让那些自己读 env 代理的库（huggingface_hub/transformers、requests…）也能直连。

    必须在 transformers/huggingface_hub **首次 import 之前**调用才能根治
    「HF 校验走死代理→http client 坏掉→缓存模型也加载不了」的坑。返回生效的 proxy。
    """
    proxy = resolve_proxy()
    if proxy is None:                      # 直连：清掉死代理（Clash 关但 env 残留 7897 会害人）
        for v in _PROXY_ENV_VARS:
            os.environ.pop(v, None)
    return proxy


def make_httpx_client(force_direct: bool = False, timeout=None):
    """
    给 OpenAI SDK 用的 httpx.Client，按策略显式设代理（trust_env=False 不继承系统 env）。
    传给 OpenAI(http_client=...)。
    """
    import httpx
    proxy = resolve_proxy(force_direct)
    kwargs = dict(trust_env=False)
    if proxy:
        kwargs["proxy"] = proxy
    if timeout is not None:
        kwargs["timeout"] = timeout
    return httpx.Client(**kwargs)
