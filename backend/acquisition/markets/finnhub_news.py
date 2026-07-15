"""Finnhub 海外新闻出网门面 —— company_news（美股个股）/ general_news（全球市场）。

13.4-2 债1 收编：Finnhub SDK 自带 requests session，此前散在 news_engine 引擎层
直接出网（绕过 acquisition 唯一出网入口，且强制 trust_env=False 直连、不吃海外代理）。
现下沉到 acquisition，并按 net.overseas 策略注入海外代理（Clash/直连自适配）：
resolve_overseas_proxy() 有值就走它，None（auto 探到可直连 / 显式 direct）则禁 env
继承直连——避免国内 Clash 未启动时 ambient HTTP_PROXY 触发 ECONNREFUSED。

返回 Finnhub 原始条目 list[dict]（headline/url/source/datetime/summary/image），
映射成业务文章格式的活留在 news_engine（引擎层职责）。
"""
import os
from typing import Any

from loguru import logger


def _client() -> Any:
    """构造 finnhub 客户端并按海外策略注入代理；FINNHUB_API_KEY 未配置返回 None。"""
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        return None
    import finnhub

    from acquisition.channels import resolve_overseas_proxy

    client = finnhub.Client(api_key=key)
    proxy = resolve_overseas_proxy()
    if proxy:
        client._session.proxies = {"http": proxy, "https": proxy}
    else:
        # 无代理（auto 探到可直连 / 显式 direct）：禁 env 继承，避免 Clash 未启 ECONNREFUSED
        client._session.trust_env = False
    return client


def fetch_company_news(symbol: str, *, date_from: str, date_to: str) -> list[dict[str, Any]]:
    """美股个股新闻（Finnhub company_news，按 ticker 精确过滤）。

    date_from/date_to: 'YYYY-MM-DD'。返回原始条目列表；key 未配或抓取失败返回 []。
    """
    client = _client()
    if client is None:
        logger.warning("FINNHUB_API_KEY 未配置，跳过美股个股新闻抓取")
        return []
    try:
        news: list[dict[str, Any]] = client.company_news(symbol, _from=date_from, to=date_to) or []
        return news
    except Exception as e:  # noqa: BLE001 — 抓取失败按空处理，不拖垮调用方
        logger.error(f"Finnhub 美股个股新闻抓取失败 {symbol}: {e}")
        return []


def fetch_general_news() -> list[dict[str, Any]]:
    """全球市场新闻（Finnhub general_news）。返回原始条目列表；key 未配或抓取失败返回 []。"""
    client = _client()
    if client is None:
        logger.warning("FINNHUB_API_KEY 未配置，跳过全球新闻抓取")
        return []
    try:
        news: list[dict[str, Any]] = client.general_news("general", min_id=0) or []
        return news
    except Exception as e:  # noqa: BLE001 — 抓取失败按空处理，不拖垮调用方
        logger.error(f"Finnhub 新闻抓取失败: {e}")
        return []
