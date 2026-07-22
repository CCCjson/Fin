"""加密新闻 / 交易所公告取数 —— 零 key 免费源（币安公告 + 主流媒体 RSS）。

## 为什么加密**必须**有事件源

日线技术指标永远看不见「这个币明天被币安下架」「协议被盗 2 亿」「SEC 起诉」。股票有
停牌和信披缓冲，加密没有——消息出来价格几分钟走完。所以事件层在加密里不是锦上添花，
是**否决闸**：宁可错过，不可踩雷。

## 两个源（实测 2026-07-21 零 key 可取）

- **币安公告**（`bapi/.../cms/article/list/query`）：不带 catalogId 时**一次请求返回全部
  8 个栏目**（上新 48 / 下架 161 / 维护 157 / 活动 93 …），最省请求。`161 Delisting`
  是硬否决的主源。
- **媒体 RSS**：CoinDesk / Cointelegraph / The Block，标准 RSS 2.0，用 stdlib
  `xml.etree` 解析——**不引入 feedparser 依赖**。

## 出网合规

走 `acquisition/crawler/base.BaseCrawler`（自带 `bounded_get` 响应封顶，防 22GB 那类
事故）+ `Channel.OVERSEAS`。业务侧的关键词分类/情绪打分不在这里，在
`crypto_intel_engine/news.py`（本模块只取数不判断）。
"""
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

from loguru import logger

from acquisition.channels import Channel
from acquisition.crawler.base import BaseCrawler

_ANN_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
_ANN_ARTICLE_BASE = "https://www.binance.com/en/support/announcement"

# 媒体 RSS 源（零 key）。名字进 NewsArticle.source。
RSS_FEEDS: dict[str, str] = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "TheBlock": "https://www.theblock.co/rss.xml",
}

# 币安公告栏目（实测；下架/维护是排雷用的重点栏目）
CATALOG_LISTING = 48
CATALOG_DELISTING = 161
CATALOG_MAINTENANCE = 157


def _crawler(timeout: int = 20) -> BaseCrawler:
    """境外通道 + 温和限速（公告/RSS 都是低频接口，别把人家打急了）。"""
    return BaseCrawler(channel=Channel.OVERSEAS, timeout=timeout, min_interval=0.3,
                       headers={"Accept": "application/json,text/xml,*/*"})


def _ms_to_dt(ms: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


# ──────────────────── 币安公告 ────────────────────


def fetch_binance_announcements(page_size: int = 20,
                                catalog_id: int | None = None) -> list[dict]:
    """币安公告。不给 catalog_id 则**一次拿回全部栏目**（最省请求）。

    Returns:
        [{catalog_id, catalog_name, title, code, url, published_at}]，新→旧未排序保持源序。
    """
    params: dict[str, Any] = {"type": 1, "pageNo": 1, "pageSize": page_size}
    if catalog_id is not None:
        params["catalogId"] = catalog_id

    data = _crawler().get_json(_ANN_URL, params=params)
    if not isinstance(data, dict) or data.get("code") != "000000":
        logger.warning(f"币安公告取数异常: code={(data or {}).get('code') if data else 'None'}")
        return []

    out: list[dict] = []
    for cat in ((data.get("data") or {}).get("catalogs") or []):
        cid, cname = cat.get("catalogId"), cat.get("catalogName")
        for art in (cat.get("articles") or []):
            code = art.get("code")
            out.append({
                "catalog_id": cid,
                "catalog_name": cname,
                "title": (art.get("title") or "").strip(),
                "code": code,
                "url": f"{_ANN_ARTICLE_BASE}/{code}" if code else None,
                "published_at": _ms_to_dt(art.get("releaseDate")),
            })
    return out


# ──────────────────── 媒体 RSS ────────────────────


def parse_rss(xml_bytes: bytes, source: str) -> list[dict]:
    """纯函数：RSS 2.0 字节流 → [{title, url, summary, source, published_at}]。

    只认标准 `channel/item`；坏 XML 返空而不是抛（一个源挂了不该拖垮整轮抓取）。
    """
    if not xml_bytes:
        return []
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as e:
        logger.warning(f"RSS 解析失败 {source}: {e}")
        return []

    out: list[dict] = []
    for item in root.iterfind(".//channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        out.append({
            "title": title,
            "url": link,
            "summary": (item.findtext("description") or "").strip()[:2000],
            "source": source,
            "published_at": _parse_rfc822(item.findtext("pubDate")),
        })
    return out


def _parse_rfc822(s: str | None) -> datetime | None:
    """RSS 的 pubDate（RFC 822，如 'Mon, 21 Jul 2026 08:00:00 +0000'）→ UTC datetime。"""
    if not s:
        return None
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_crypto_news(sources: dict[str, str] | None = None) -> list[dict]:
    """抓全部 RSS 源。单源失败只跳过该源（记 warning），不影响其它源。"""
    feeds = sources if sources is not None else RSS_FEEDS
    crawler = _crawler()
    out: list[dict] = []
    for name, url in feeds.items():
        try:
            cap = crawler.get(url, mode="bytes")
            if cap is None or cap.data is None:
                logger.warning(f"RSS 抓取为空: {name}")
                continue
            out.extend(parse_rss(cap.data, name))
        except Exception as e:  # noqa: BLE001 — 单源失败不拖垮整轮
            logger.warning(f"RSS 抓取失败 {name}: {e}")
    return out
