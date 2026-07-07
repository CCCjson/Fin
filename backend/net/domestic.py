"""
国内数据源（东财 eastmoney / akshare）的 Clash-无关抓取原语。

两个入口：
- domestic_get / domestic_json：东财 REST 直调。快代理池 → 失败换 IP → 直连兜底。
- domestic_akshare：包一层执行 akshare 调用，用 env 注入快代理 + 轮换 + 直连兜底，
  保证「无论 Clash 开没开都能爬」。因改的是进程全局 env，内部用锁串行化。
"""
import threading
from typing import Any, Callable, Dict, Optional

import requests
from loguru import logger

from net.env import proxy_env
from net.proxy_manager import ProxyManager
from net.session import make_domestic_session

# akshare 调用串行化：domestic_akshare 通过全局 env 注入代理，必须互斥
_AKSHARE_LOCK = threading.Lock()


def get_proxy_manager() -> Optional[ProxyManager]:
    """创建一个 ProxyManager（未配置快代理时返回 None）。"""
    try:
        pm = ProxyManager()
        return pm if pm.api_url else None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"无法创建 ProxyManager: {e}")
        return None


def domestic_get(
    url: str,
    *,
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: int = 8,
    max_rounds: int = 3,
    use_proxy_pool: bool = True,
    proxy_mgr: Optional[ProxyManager] = None,
) -> Optional[requests.Response]:
    """GET 一个国内 URL：快代理池逐轮换 IP 重试，最后一轮直连兜底。

    Returns:
        成功的 requests.Response；全部失败返回 None（不抛）。
    """
    pm = proxy_mgr if proxy_mgr is not None else (get_proxy_manager() if use_proxy_pool else None)
    total_rounds = max(1, max_rounds)

    for attempt in range(total_rounds):
        proxies = None
        is_last = attempt == total_rounds - 1
        if pm and not is_last:
            # 首轮 fetch，后续 switch 换新 IP；最后一轮强制直连兜底（proxies=None）
            p = pm.fetch_one_proxy() if attempt == 0 else pm.switch_proxy()
            if p:
                proxies = p.to_requests_proxies()
                logger.debug(f"domestic_get 使用快代理: {p.ip}:{p.port}（第 {attempt + 1} 轮）")
        try:
            session = make_domestic_session(proxies)
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:  # noqa: BLE001
            logger.warning(f"domestic_get 第 {attempt + 1}/{total_rounds} 轮失败: {e}")
            continue

    return None


def domestic_json(url: str, **kwargs) -> Optional[dict]:
    """domestic_get 的 .json() 版；失败或非 JSON 返回 None。"""
    resp = domestic_get(url, **kwargs)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError as e:
        logger.warning(f"domestic_json 解析失败: {e}")
        return None


def domestic_akshare(
    fn: Callable[..., Any],
    *args,
    use_proxy_pool: bool = True,
    max_rounds: int = 3,
    **kwargs,
) -> Any:
    """在「Clash-无关」环境下执行一个 akshare 调用。

    优先用快代理 IP（env 注入）逐轮换 IP 重试；全失败则清 env 直连兜底。
    akshare 内部走 requests 读 env 代理，故用 proxy_env 上下文覆盖 HTTP(S)_PROXY，
    并用 _AKSHARE_LOCK 串行化（改的是进程全局 env）。

    Args:
        fn: akshare 函数，如 ak.stock_news_em
        *args/**kwargs: 透传给 fn
    Returns:
        fn 的返回值；全部失败时返回最后一次直连的结果（可能抛 fn 自身异常）。
    """
    with _AKSHARE_LOCK:
        pm = get_proxy_manager() if use_proxy_pool else None
        total_rounds = max(1, max_rounds)

        for attempt in range(total_rounds):
            proxy_url = None
            if pm:
                p = pm.fetch_one_proxy() if attempt == 0 else pm.switch_proxy()
                proxy_url = p.to_env_url() if p else None
            with proxy_env(proxy_url):
                try:
                    r = fn(*args, **kwargs)
                    # DataFrame 为空视为失败，换 IP 重试
                    if r is not None and not (hasattr(r, "empty") and r.empty):
                        return r
                    logger.debug(f"akshare 第 {attempt + 1} 轮返回空，换 IP 重试")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"akshare 第 {attempt + 1}/{total_rounds} 轮失败: {e}")

        # 兜底：清 env 强制直连东财，直接返回（异常透传给调用方）
        with proxy_env(None):
            return fn(*args, **kwargs)
