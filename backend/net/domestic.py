"""
国内数据源（东财 eastmoney / akshare）的 Clash-无关抓取原语。

三个入口，共用同一个换 IP 轮换驱动 `_rotate`：
- domestic_get / domestic_json：东财 REST 直调，返回裸 Response。
- domestic_bounded_get：同上但**响应封顶**（流式，超限截断/落盘），大响应用它。
- domestic_akshare：包一层执行 akshare 调用，用 env 注入快代理 + 轮换。因改的是进程全局
  env，内部用锁串行化。

⛔ 绝不直连兜底（2026-07-08 拍板）：国内高频防爬接口直连会被服务端直接掐断连接
（`ProtocolError('Connection aborted')`），失败只能换新 IP 重试，换到底还失败就
让调用方失败/下次再补，不允许静默退化成本地直连（见 memory proxy-notes 铁律）。
唯一例外是 `ProxyManager` 压根没配置（`.env` 无 `kuaidaili_api`）——这不是「重试
失败后降级」，是根本没有代理服务可用，直连是唯一选项。

铁律活在 `_rotate` 一处：谁想加一条国内抓取路径，就得从这里过。门禁见
`tests/net/test_no_direct_fallback.py`。
"""
import threading
from typing import Any, Callable, Dict, Optional

import requests
from loguru import logger

from net.bounded import Capped, bounded_get
from net.env import proxy_env

# 单例住在 proxy_manager.py；这里再导出，历史调用方（含测试）打的是 `net.domestic.*`
from net.proxy_manager import (
    ProxyInfo,
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


def _rotate(
    run: Callable[[Optional[ProxyInfo]], Any],
    *,
    what: str,
    pm: Optional[ProxyManager],
    max_rounds: int,
    prefer_direct: bool,
    empty_is_failure: Optional[Callable[[Any], bool]] = None,
    reraise_on_direct_failure: bool = False,
) -> Any:
    """逐轮换快代理 IP 执行 `run(proxy)`，绝不降级直连。**铁律的唯一实现处。**

    `run` 收到的 `proxy` 为 None 时表示本轮显式直连——只有两种情况会发生：
    未配置快代理（`pm is None`），或调用方点头的 `prefer_direct` 第 0 轮。
    「取不到 IP 就 proxies=None」这条降级路径在这里被结构性堵死。

    Args:
        run: 单轮执行体。抛异常 = 本轮失败，换下一个 IP 重试。
        what: 出现在日志/异常里的操作名，如 `GET https://...` 或 `stock_news_em`。
        pm: 代理管理器；None = `.env` 没配快代理，直连是唯一选项。
        prefer_direct: 第 0 轮先试直连，失败了再上快代理（低频接口省额度用）。
            这**不是**降级——铁律禁止的是「取不到 IP 时悄悄 proxies=None」，
            而这里直连是调用方显式点头的第一次尝试。一旦直连失败就必须走代理，
            代理取不到 IP 照样抛 `ProxyExhaustedError`，绝不回头再试直连。
        empty_is_failure: 判定「返回值虽无异常但等于失败」（akshare 空 DataFrame）。
            **只在配了快代理时生效**——没代理可换时，空结果就是真实结果。
        reraise_on_direct_failure: 未配代理且直连抛异常时，透传异常（akshare）而非
            吞掉返回 None（domestic_get）。两者语义本就不同，这里显式区分。

    Returns:
        `run` 首次成功的返回值；未配代理且全轮失败时返回 None。

    Raises:
        ProxyExhaustedError: 配了快代理却取不到 IP，或换满 `max_rounds` 轮仍全败。
    """
    total_rounds = max(1, max_rounds)
    # prefer_direct 只在「有代理可退守」时才谈得上前进式直连；没代理时每轮都是直连。
    direct_round = 0 if (prefer_direct and pm) else -1

    for attempt in range(total_rounds):
        proxy: Optional[ProxyInfo] = None
        if pm is not None and attempt != direct_round:
            # 首个走代理的轮次复用当前 IP（没过期就不扣额度）；之后每轮 switch 换新 IP。
            # 直接 fetch_one_proxy() 等于每次请求都买一个新 IP。
            proxy = pm.get_proxy() if attempt == direct_round + 1 else pm.switch_proxy()
            if not proxy:
                # 配了快代理却取不到 IP（额度耗尽 / API 挂）。**绝不用 proxies=None
                # 发请求** —— 那就是降级直连，铁律禁止（commit 7158f37）。
                # 也不再把剩余轮次打完：那只是白白轰炸提取 API。
                raise ProxyExhaustedError(
                    f"取不到快代理 IP（额度耗尽？），拒绝降级直连，放弃 {what}")
            logger.debug(f"{what} 使用快代理: {proxy.ip}:{proxy.port}（第 {attempt + 1} 轮）")

        try:
            result = run(proxy)
        except Exception as e:  # noqa: BLE001  # 任何失败都只意味着「换个 IP 再来」
            # 未配快代理时没有可换的 IP：akshare 透传异常，domestic_get 吞掉重试。
            if pm is None and reraise_on_direct_failure:
                raise
            logger.warning(f"{what} 第 {attempt + 1}/{total_rounds} 轮失败: {e}")
            continue

        if pm is None or empty_is_failure is None or not empty_is_failure(result):
            return result
        logger.debug(f"{what} 第 {attempt + 1} 轮返回空，换 IP 重试")

    if pm is not None:
        raise ProxyExhaustedError(
            f"快代理连续换 {total_rounds} 轮仍取不到 IP 或全部失败，放弃 {what}")
    return None   # 未配置快代理：直连是唯一选项，它也失败了


def _resolve_pm(
    use_proxy_pool: bool, proxy_mgr: Optional[ProxyManager]
) -> Optional[ProxyManager]:
    if proxy_mgr is not None:
        return proxy_mgr
    return get_proxy_manager() if use_proxy_pool else None


def _proxies_of(proxy: Optional[ProxyInfo]) -> Optional[Dict[str, str]]:
    return proxy.to_requests_proxies() if proxy else None


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

    响应**无上限**读进内存。已知国内行情响应最大 ~235KB（K 线全历史），够用；
    抓不确定大小的东西（网页/PDF/大 JSON）请改用 `domestic_bounded_get`。

    Args:
        prefer_direct: 见 `_rotate` 同名参数。

    Returns:
        成功的 requests.Response；未配代理且全部失败返回 None（不抛）。
    """
    def _run(proxy: Optional[ProxyInfo]) -> requests.Response:
        session = make_domestic_session(_proxies_of(proxy))
        resp = session.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp

    return _rotate(
        _run,
        what=f"GET {url}",
        pm=_resolve_pm(use_proxy_pool, proxy_mgr),
        max_rounds=max_rounds,
        prefer_direct=prefer_direct,
    )


def domestic_bounded_get(
    url: str,
    *,
    mode: str = "bytes",
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: int = 8,
    max_rounds: int = 5,
    use_proxy_pool: bool = True,
    prefer_direct: bool = False,
    proxy_mgr: Optional[ProxyManager] = None,
    max_bytes: Optional[int] = None,
    file_suffix: str = ".bin",
) -> Optional[Capped]:
    """两条铁律合一：国内换 IP 轮换（`_rotate`）+ 响应流式封顶（`bounded_get`）。

    HTTP 4xx/5xx 触发换 IP 重试（与 `domestic_get` 的 `raise_for_status` 对齐）。
    `too_large`（Content-Length 预判超限）是**真实答案不是失败**，直接返回，不重试
    ——换个 IP 那份响应照样超限。

    Returns:
        Capped；未配代理且全部失败返回 None。
    """
    def _run(proxy: Optional[ProxyInfo]) -> Capped:
        session = make_domestic_session(_proxies_of(proxy))
        cap = bounded_get(
            session, url, mode=mode, params=params, headers=headers,
            timeout=timeout, max_bytes=max_bytes, file_suffix=file_suffix,
        )
        if cap.status >= 400:
            raise requests.HTTPError(f"HTTP {cap.status} for {url}")
        return cap

    return _rotate(
        _run,
        what=f"GET(bounded) {url}",
        pm=_resolve_pm(use_proxy_pool, proxy_mgr),
        max_rounds=max_rounds,
        prefer_direct=prefer_direct,
    )


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


def _akshare_result_is_empty(r: Any) -> bool:
    """akshare 返回空 DataFrame / None 视为本轮失败（服务端在防爬），换 IP 重试。"""
    return r is None or bool(hasattr(r, "empty") and r.empty)


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

    未配置快代理（`ProxyManager.api_url` 为空）时每轮都是直连——这不是「重试
    失败后降级」，是压根没有代理服务可用，直连是唯一选项；此时空结果就是真实
    结果（不重试），异常直接透传给调用方。

    Args:
        fn: akshare 函数，如 ak.stock_news_em
        prefer_direct: 第 0 轮先试直连，失败再上快代理（见 `_rotate` 同名参数）。
        *args/**kwargs: 透传给 fn
    Returns:
        fn 的返回值。
    Raises:
        ProxyExhaustedError: 配置了快代理但换 IP 到 max_rounds 轮仍全部失败/返回空。
        fn 自身抛出的异常：仅在未配置快代理（纯直连模式）时透传。
    """
    with _AKSHARE_LOCK:
        def _run(proxy: Optional[ProxyInfo]) -> Any:
            with proxy_env(proxy.to_env_url() if proxy else None):
                return fn(*args, **kwargs)

        return _rotate(
            _run,
            what=fn.__name__,
            pm=get_proxy_manager() if use_proxy_pool else None,
            max_rounds=max_rounds,
            prefer_direct=prefer_direct,
            empty_is_failure=_akshare_result_is_empty,
            reraise_on_direct_failure=True,
        )
