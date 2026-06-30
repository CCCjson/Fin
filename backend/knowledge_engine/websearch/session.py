"""
HTTP 会话层 — 隔离 curl_cffi 细节，给 DDG / read_url / EDGAR 复用。

移植自 Scrapper capitalIQ `create_session` 的「curl_cffi 指纹 + 可选代理」核心，
**只抄壳，不碰 token/2FA/playwright/真实密钥**。常量抄自 Scrapper config。

★ 代理兼容（Clash / 本地直连 自动适配）：默认不继承系统 HTTPS_PROXY（trust_env=False），
按 get_websearch_proxy() 策略显式决定走 Clash 还是直连。这样 Clash 关着、或将来本地能
直接翻墙（新加坡），websearch 都能用，无需改配置。
"""
from knowledge_engine.config import get_scraper_proxy
from net_proxy import resolve_proxy as _resolve_base, clash_alive, reset_probe  # noqa: F401

# 抄自 Scrapper config：chrome131 在该版 curl_cffi 有 TLS bug，统一用 chrome120 指纹
IMPERSONATE = "chrome120"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120


def reset_proxy_probe() -> None:
    """清掉探测缓存（Clash 状态变化后想立即重判时调用）。"""
    reset_probe()


def resolve_proxy(force_direct: bool = False) -> str | None:
    """
    解析本次该用的代理 URL，None 表示直连。
    显式反爬代理 KNOWLEDGE_SCRAPER_PROXY 优先；否则走全局统一策略 net_proxy（HTTP_PROXY_MODE）。
    """
    if force_direct:
        return None
    sp = get_scraper_proxy()                     # 强反爬专用代理优先
    if sp:
        return sp
    return _resolve_base()                        # 全系统统一 auto/direct/url


def make_cffi_session(force_direct: bool = False):
    """DDG / 强反爬源用：TLS 指纹伪装 + 按策略显式设代理。"""
    from curl_cffi import requests as cffi
    s = cffi.Session(impersonate=IMPERSONATE, trust_env=False)
    proxy = resolve_proxy(force_direct)
    # 显式设代理：有则走它，无则空串强制直连。
    # 注意：curl_cffi 的 trust_env=False 不够可靠（C 层仍会读 env 代理），
    # 必须显式空串才能 100% 覆盖系统 ALL_PROXY/HTTPS_PROXY。
    s.proxies = {"http": proxy, "https": proxy} if proxy else {"http": "", "https": ""}
    return s


def make_plain_session(force_direct: bool = False):
    """SEC EDGAR / 普通网页下载用：plain requests，按策略显式设代理。"""
    import requests
    s = requests.Session()
    s.trust_env = False                          # requests 这个足够禁用 env 代理
    proxy = resolve_proxy(force_direct)
    s.proxies = {"http": proxy, "https": proxy} if proxy else {"http": "", "https": ""}
    return s
