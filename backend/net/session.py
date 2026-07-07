"""
统一的 HTTP session/client 工厂。

- make_domestic_session()：国内数据源用，trust_env=False 绕开系统 Clash，
  只用显式传入的（快）代理。收敛了原先散落 4+ 份的 _make_session。
- make_httpx_client()：给 OpenAI SDK 用的 httpx.Client，按 Clash 策略显式设代理。
"""
from typing import Dict, Optional

import requests

from net.clash import resolve_proxy


def make_domestic_session(proxies: Optional[Dict[str, str]] = None) -> requests.Session:
    """创建绕过系统代理的 Session（国内东财/akshare 数据源用）。

    系统可能配置了 Clash/V2Ray 本地代理 (127.0.0.1:7897)，并发请求会压垮它。
    用 trust_env=False 忽略系统代理，只使用显式传入的快代理。
    """
    session = requests.Session()
    session.trust_env = False
    if proxies:
        session.proxies.update(proxies)
    return session


def make_httpx_client(force_direct: bool = False, timeout=None):
    """给 OpenAI SDK 用的 httpx.Client，按策略显式设代理（trust_env=False）。"""
    import httpx
    proxy = resolve_proxy(force_direct)
    kwargs = dict(trust_env=False)
    if proxy:
        kwargs["proxy"] = proxy
    if timeout is not None:
        kwargs["timeout"] = timeout
    return httpx.Client(**kwargs)
