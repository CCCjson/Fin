"""
海外出口自适配 —— 先探本地网络能否直连海外，能就直连，不能才走 Shadowrocket(7898)。

跟 net/clash 那套对称，但方向相反：Clash 是"探到就走"，海外是"直连优先、代理兜底"。
Jason 的 wifi 能直翻时直连（更快）；换到不能翻的网络自动切 7898，零配置。

统一开关 env `KNOWLEDGE_OVERSEAS_MODE`（默认 auto）：
- auto  ：socket 探测直连海外(google:443)通不通；通→直连，不通→走 KNOWLEDGE_OVERSEAS_PROXY。
- direct ：强制直连（本地确定能翻墙时）。
- proxy  ：强制走 KNOWLEDGE_OVERSEAS_PROXY（不探测）。
"""
import os
import socket
import threading

# 直连海外探针：google:443 —— 能翻墙时可连，被墙时 timeout/RST。用它判"本地能否直接出海外"。
_CANARY = ("www.google.com", 443)
_probe = {"ok": None}
_lock = threading.Lock()


def get_overseas_mode() -> str:
    return os.getenv("KNOWLEDGE_OVERSEAS_MODE", "auto")


def get_overseas_proxy() -> str:
    """海外出口代理（Shadowrocket），形如 http://127.0.0.1:7898。空=无。"""
    return os.getenv("KNOWLEDGE_OVERSEAS_PROXY", "")


def overseas_direct_ok() -> bool:
    """探测本地网络能否直连海外（进程内缓存一次）。"""
    with _lock:
        if _probe["ok"] is None:
            try:
                s = socket.create_connection(_CANARY, timeout=4)
                s.close()
                _probe["ok"] = True
            except OSError:
                _probe["ok"] = False
        return _probe["ok"]


def reset_overseas_probe() -> None:
    """清掉探测缓存（网络/Shadowrocket 状态变化后想立即重判时调用）。"""
    with _lock:
        _probe["ok"] = None


def resolve_overseas() -> str | None:
    """
    解析海外站该用的出口：返回代理 URL（走 Shadowrocket）或 None（直连）。
    auto=先探直连,通就 None,不通返代理; direct=None; proxy=代理。
    """
    mode = (get_overseas_mode() or "auto").strip().lower()
    if mode == "direct":
        return None
    proxy = get_overseas_proxy() or None
    if mode == "proxy":
        return proxy
    # auto：本地能直连海外就直连，否则走代理兜底
    return None if overseas_direct_ok() else proxy
