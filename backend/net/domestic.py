"""
国内数据源（东财 eastmoney / akshare）的 Clash-无关抓取原语。

两个入口：
- domestic_get / domestic_json：东财 REST 直调。快代理池 → 失败换 IP → 换到底还失败就返回 None。
- domestic_akshare：包一层执行 akshare 调用，用 env 注入快代理 + 轮换。因改的是进程全局
  env，内部用锁串行化。

⛔ 绝不直连兜底（2026-07-08 拍板）：国内高频防爬接口直连会被服务端直接掐断连接
（`ProtocolError('Connection aborted')`），失败只能换新 IP 重试，换到底还失败就
让调用方失败/下次再补，不允许静默退化成本地直连（见 memory proxy-notes 铁律）。
唯一例外是 `ProxyManager` 压根没配置（`.env` 无 `kuaidaili_api`）——这不是「重试
失败后降级」，是根本没有代理服务可用，直连是唯一选项。
"""
import threading
from typing import Any, Callable, Dict, Optional

import requests
from loguru import logger

from net.env import proxy_env

# 单例住在 proxy_manager.py；这里再导出，历史调用方（含测试）打的是 `net.domestic.*`
from net.proxy_manager import (
    ProxyManager,
)
from net.proxy_manager import (
    get_proxy_manager as get_proxy_manager,
)
from net.proxy_manager import (
    reset_proxy_manager as reset_proxy_manager,
)
from net.session import make_domestic_session

# akshare 调用串行化：domestic_akshare 通过全局 env 注入代理，必须互斥
_AKSHARE_LOCK = threading.Lock()




class ProxyExhaustedError(RuntimeError):
    """快代理连续换 IP 仍全部失败，本次调用放弃（不降级直连）。"""


def domestic_get(
    url: str,
    *,
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: int = 8,
    max_rounds: int = 5,
    use_proxy_pool: bool = True,
    prefer_direct: bool = False,
    proxy_mgr: Optional[ProxyManager] = None,
) -> Optional[requests.Response]:
    """GET 一个国内 URL：快代理池逐轮换 IP 重试，绝不直连兜底。

    Args:
        prefer_direct: 第 0 轮先试本地直连，失败了再上快代理（低频接口省额度用，
            如新闻）。这**不是**降级——铁律禁止的是「取不到 IP 就悄悄 proxies=None」，
            而这里直连是调用方显式点头的第一次尝试。一旦直连失败就必须走代理，
            代理取不到 IP 照样抛 `ProxyExhaustedError`，绝不回头再试直连。

    Returns:
        成功的 requests.Response；全部失败返回 None（不抛）。
    """
    pm = proxy_mgr if proxy_mgr is not None else (get_proxy_manager() if use_proxy_pool else None)
    total_rounds = max(1, max_rounds)
    direct_round = 0 if (prefer_direct and pm) else -1

    for attempt in range(total_rounds):
        proxies = None
        if pm and attempt != direct_round:
            # 首个走代理的轮次复用当前 IP（没过期就不扣额度）；之后每轮 switch 换新 IP。
            # 直接 fetch_one_proxy() 等于每次请求都买一个新 IP。
            p = pm.get_proxy() if attempt == direct_round + 1 else pm.switch_proxy()
            if not p:
                # 配了快代理却取不到 IP（额度耗尽 / API 挂）。**绝不用 proxies=None
                # 发请求** —— 那就是降级直连，铁律禁止（commit 7158f37）。
                # 也不再把剩余轮次打完：那只是白白轰炸提取 API。
                raise ProxyExhaustedError(
                    f"取不到快代理 IP（额度耗尽？），拒绝降级直连，放弃 GET {url}")
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

    if pm:
        raise ProxyExhaustedError(
            f"快代理连续换 {total_rounds} 轮仍取不到 IP 或全部失败，放弃 GET {url}"
        )
    return None   # 未配置快代理：直连是唯一选项，它也失败了


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
    max_rounds: int = 5,
    prefer_direct: bool = False,
    **kwargs,
) -> Any:
    """在「Clash-无关」环境下执行一个 akshare 调用。

    用快代理 IP（env 注入）逐轮换 IP 重试，绝不降级直连（东财等高频接口直连
    会被服务端直接掐断，见 memory proxy-notes 铁律）。akshare 内部走 requests
    读 env 代理，故用 proxy_env 上下文覆盖 HTTP(S)_PROXY，并用 _AKSHARE_LOCK
    串行化（改的是进程全局 env）。

    未配置快代理（`ProxyManager.api_url` 为空）时 `pm` 为 None，每轮都是直连——
    这不是「重试失败后降级」，是压根没有代理服务可用，直连是唯一选项。

    Args:
        fn: akshare 函数，如 ak.stock_news_em
        prefer_direct: 第 0 轮先试直连，失败再上快代理（见 domestic_get 同名参数）。
        *args/**kwargs: 透传给 fn
    Returns:
        fn 的返回值。
    Raises:
        ProxyExhaustedError: 配置了快代理但换 IP 到 max_rounds 轮仍全部失败/返回空。
        fn 自身抛出的异常：仅在未配置快代理（纯直连模式）时透传。
    """
    with _AKSHARE_LOCK:
        pm = get_proxy_manager() if use_proxy_pool else None
        total_rounds = max(1, max_rounds)
        direct_round = 0 if (prefer_direct and pm) else -1

        for attempt in range(total_rounds):
            proxy_url = None
            if pm and attempt != direct_round:
                # 同 domestic_get：首个代理轮复用当前 IP，之后每轮换新。
                p = pm.get_proxy() if attempt == direct_round + 1 else pm.switch_proxy()
                if not p:
                    # 取不到 IP 时 proxy_env(None) 就是直连——不许发请求，也别把
                    # 剩余轮次打完（白白轰炸提取 API）。
                    raise ProxyExhaustedError(
                        f"取不到快代理 IP（额度耗尽？），拒绝降级直连，放弃 {fn.__name__}")
                proxy_url = p.to_env_url()
            with proxy_env(proxy_url):
                try:
                    r = fn(*args, **kwargs)
                    # DataFrame 为空视为失败，换 IP 重试
                    if r is not None and not (hasattr(r, "empty") and r.empty):
                        return r
                    if not pm:
                        return r  # 未配快代理：纯直连，空结果就是真实结果，不重试
                    logger.debug(f"akshare 第 {attempt + 1} 轮返回空，换 IP 重试")
                except Exception as e:  # noqa: BLE001
                    if not pm:
                        raise  # 未配快代理：没有可换的 IP，直接透传异常
                    logger.warning(f"akshare 第 {attempt + 1}/{total_rounds} 轮失败: {e}")

        raise ProxyExhaustedError(
            f"快代理连续换 {total_rounds} 轮仍全部失败/返回空，放弃本次 {fn.__name__} 调用"
        )
