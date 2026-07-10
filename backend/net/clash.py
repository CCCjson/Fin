"""
Clash 探测与国外出网代理策略。

统一开关 env `HTTP_PROXY_MODE`（默认 auto）：
- auto      ：socket 探测 Clash(127.0.0.1:7897)，在线→走 Clash，离线→直连。
- direct/none：强制直连。
- 具体 URL  ：强制走该代理。

与最早那版 net_proxy 的区别：`clash_alive()` 改为**带短 TTL 的实时重探**
（默认 5 秒），Clash 开/关后最多 5 秒自适应，不再需要重启进程。
"""
import os
import socket
import threading
import time

_CLASH_HOST, _CLASH_PORT = "127.0.0.1", 7897
_PROBE_TTL = 5.0  # 秒；探测结果缓存时长

# {"alive": bool | None, "ts": monotonic 时间戳}
_probe = {"alive": None, "ts": 0.0}
_lock = threading.Lock()


def clash_alive() -> bool:
    """socket 探测 Clash 7897 是否在线（带 TTL 缓存，过期自动重探）。"""
    with _lock:
        now = time.monotonic()
        if _probe["alive"] is None or (now - _probe["ts"]) >= _PROBE_TTL:
            try:
                s = socket.create_connection((_CLASH_HOST, _CLASH_PORT), timeout=0.5)
                s.close()
                _probe["alive"] = True
            except OSError:
                _probe["alive"] = False
            _probe["ts"] = now
        return _probe["alive"]


def reset_probe() -> None:
    """清掉探测缓存（想立即重判 Clash 状态时调用）。"""
    with _lock:
        _probe["alive"] = None
        _probe["ts"] = 0.0


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
