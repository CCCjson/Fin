"""
爬虫代理按域名路由 — 国内域名走快代理池，海外走 Shadowrocket。

- 国内（get_domestic_domains 命中）：net/proxy_manager 取快代理 IP（轮换抗封）。
  ⛔ 取不到 IP → 抛 ProxyExhaustedError，**绝不降级直连**（铁律，与 net/domestic 同）。
  唯一例外：显式 KNOWLEDGE_SCRAPER_PROXY_ENABLED=false，那是明示选择直连。
- 海外：resolve_overseas() —— 先探本地能否直连海外，能就直连(更快)，不通才走
  Shadowrocket(KNOWLEDGE_OVERSEAS_PROXY)。KNOWLEDGE_OVERSEAS_MODE=auto/direct/proxy。

两种消费格式：
  - playwright_proxy_for(url) → {server, username?, password?}（给 BrowserSession）
  - curl_proxy_for(url)       → "http://...:port" 单串（给 fast_fetch / curl_cffi）
"""
import threading
from urllib.parse import urlparse
from typing import Optional


from knowledge_engine.config import (
    get_domestic_domains,
    get_scraper_proxy_enabled, get_proxy_fetch_timeout,
)
from net import ProxyExhaustedError
from net.overseas import resolve_overseas   # 海外：先探直连,不通走 7898

_manager = None
_manager_lock = threading.Lock()


def domain_of(url: str) -> str:
    """取 url 的域名（小写、去 www.）。"""
    try:
        net = urlparse(url if "://" in url else f"http://{url}").netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def is_domestic(url: str) -> bool:
    """域名是否属于国内清单（后缀匹配，覆盖子域）。"""
    d = domain_of(url)
    if not d:
        return False
    return any(d == dom or d.endswith("." + dom) for dom in get_domestic_domains())


def _get_manager():
    """取进程内 ProxyManager 单例（未配快代理时返回 None）。

    以前这里 `ProxyManager()` 自建一份，和 `net` 那个各持一份 IP 缓存，谁也复用不了谁。
    """
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                from net.proxy_manager import get_proxy_manager
                _manager = get_proxy_manager()
    return _manager


def _domestic_proxy_info():
    """取一个国内快代理 IP（带硬超时）。

    ⛔ 铁律：国内抓取失败只换 IP 重试，**任何场景禁止降级本地直连**。此前这里
    在「未启用 / 取 IP 失败 / 超时」三处静默返回 None（= 直连），2026-07-10
    Jason 拍板「严格统一」后一律抛 `ProxyExhaustedError`。

    Returns:
        ProxyInfo；或 None —— **仅当**显式 `KNOWLEDGE_SCRAPER_PROXY_ENABLED=false`
        （明示选择直连，等价于「没配代理」）。

    Raises:
        ProxyExhaustedError: 已启用代理但取不到 IP（额度耗尽 / API 挂 / 超时）。
    """
    if not get_scraper_proxy_enabled():
        return None   # 明示选择直连，不是降级
    import concurrent.futures

    def _fetch():
        with _manager_lock:
            mgr = _get_manager()
            # 单例为 None = 没配快代理。返回 None 走下面的 ProxyExhaustedError
            # （与旧行为一致：旧版自建的空 ProxyManager 也是 get_proxy() -> None）。
            return mgr.get_proxy() if mgr else None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            pi = ex.submit(_fetch).result(timeout=get_proxy_fetch_timeout())
    except concurrent.futures.TimeoutError as e:
        raise ProxyExhaustedError(
            f"取快代理 IP 超时（>{get_proxy_fetch_timeout()}s），拒绝降级直连") from e
    except Exception as e:
        raise ProxyExhaustedError(f"取快代理 IP 失败，拒绝降级直连：{str(e)[:80]}") from e

    if pi is None:
        raise ProxyExhaustedError("快代理取不到 IP（额度耗尽？），拒绝降级直连")
    return pi


def curl_proxy_for(url: str) -> Optional[str]:
    """给 curl_cffi 用的代理单串；None = 直连。"""
    if is_domestic(url):
        pi = _domestic_proxy_info()
        return pi.url if pi else None   # None 只可能是显式关闭代理
    return resolve_overseas()          # 海外：直连优先，不通走 7898


def playwright_proxy_for(url: str) -> Optional[dict]:
    """给 Playwright launch(proxy=) 用的 dict；None = 直连。"""
    if is_domestic(url):
        pi = _domestic_proxy_info()
        if not pi:
            return None   # 只可能是显式 KNOWLEDGE_SCRAPER_PROXY_ENABLED=false
        server = f"{pi.protocol}://{pi.ip}:{pi.port}"
        d = {"server": server}
        if pi.username and pi.password:
            d["username"] = pi.username
            d["password"] = pi.password
        return d
    ov = resolve_overseas()            # 海外：直连优先，不通走 7898
    return {"server": ov} if ov else None
