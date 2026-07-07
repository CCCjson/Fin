"""
爬虫代理按域名路由 — 国内域名走快代理池，海外走 Shadowrocket。

- 国内（get_domestic_domains 命中）：net/proxy_manager 取快代理 IP（轮换抗封）。
  未配 kuaidaili 或取 IP 失败 → 优雅降级直连，绝不阻塞。
- 海外：resolve_overseas() —— 先探本地能否直连海外，能就直连(更快)，不通才走
  Shadowrocket(KNOWLEDGE_OVERSEAS_PROXY)。KNOWLEDGE_OVERSEAS_MODE=auto/direct/proxy。

两种消费格式：
  - playwright_proxy_for(url) → {server, username?, password?}（给 BrowserSession）
  - curl_proxy_for(url)       → "http://...:port" 单串（给 fast_fetch / curl_cffi）
"""
import threading
from urllib.parse import urlparse
from typing import Optional

from loguru import logger

from knowledge_engine.config import (
    get_domestic_domains,
    get_scraper_proxy_enabled, get_proxy_fetch_timeout,
)
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
    """懒加载 ProxyManager 单例（非线程安全，取 IP 都在锁内串行）。"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                from net.proxy_manager import ProxyManager
                _manager = ProxyManager()
    return _manager


def _domestic_proxy_info():
    """取一个国内快代理 IP（带硬超时；未启用/失败/超时一律返 None → 直连）。"""
    if not get_scraper_proxy_enabled():
        return None
    import concurrent.futures

    def _fetch():
        with _manager_lock:
            return _get_manager().get_proxy()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_fetch).result(timeout=get_proxy_fetch_timeout())
    except concurrent.futures.TimeoutError:
        logger.warning(f"取快代理 IP 超时（>{get_proxy_fetch_timeout()}s），降级直连")
        return None
    except Exception as e:
        logger.warning(f"取快代理 IP 失败，降级直连：{str(e)[:80]}")
        return None


def curl_proxy_for(url: str) -> Optional[str]:
    """给 curl_cffi 用的代理单串；None = 直连。"""
    if is_domestic(url):
        pi = _domestic_proxy_info()
        return pi.url if pi else None
    return resolve_overseas()          # 海外：直连优先，不通走 7898


def playwright_proxy_for(url: str) -> Optional[dict]:
    """给 Playwright launch(proxy=) 用的 dict；None = 直连。"""
    if is_domestic(url):
        pi = _domestic_proxy_info()
        if not pi:
            return None
        server = f"{pi.protocol}://{pi.ip}:{pi.port}"
        d = {"server": server}
        if pi.username and pi.password:
            d["username"] = pi.username
            d["password"] = pi.password
        return d
    ov = resolve_overseas()            # 海外：直连优先，不通走 7898
    return {"server": ov} if ov else None
