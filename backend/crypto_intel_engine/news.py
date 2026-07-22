"""加密事件层 —— 公告/新闻的**业务判断**（取数在 `acquisition/markets/crypto_news.py`）。

## 三件事

1. **硬否决**（`check_hard_events`）：下架 / 被盗 / 监管执法这类事件，一旦命中就把排雷
   结论直接压成 `avoid`。⛔ 它**不是**打分项——「要被币安下架了」不该被技术分 85 稀释成
   「综合 62 分，建议买入」。这类事件只有一个正确反应：不碰。
2. **情绪聚合**（`aggregate_sentiment`）：复用现成的 FinBERT（`news_engine.sentiment`）
   结果，按币聚合成 -1..1 的净情绪，喂 sentiment 维。
3. **入库**（`ingest`）：公告 + RSS → `NewsArticle(market='crypto')`，复用股票那套
   去重（MD5(source+url)）与情感表，不另起炉灶。

## 币名匹配为什么要小心

新闻标题里认币靠 ticker，但 `ID`/`OP`/`SUI` 这种短 ticker 极易误伤（"OP" 出现在
"OPTIONS"、"ID" 出现在任何句子里）。规则：**≤3 字符的 ticker 只认括号形式**
`(OP)`，更长的才允许裸词边界匹配。宁可漏，不可错——错配会让一条无关新闻否决掉一个币。
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from common.market_time import utc_now

# ──────────────────── 硬否决事件规则 ────────────────────
# (正则, 事件类型, 人话说明)。全部小写匹配。
_HARD_EVENT_RULES: list[tuple[str, str, str]] = [
    (r"\b(delist|delisting|will remove|removal of (spot|margin)|notice of removal)\b",
     "delisting", "币安下架/移除交易对"),
    (r"\b(hack|hacked|exploit|exploited|stolen|breach|drained)\b",
     "security", "安全事件（被盗/被攻击）"),
    (r"\b(sec (sues|charges|sued)|lawsuit|indicted|fraud charges|enforcement action|sanctioned)\b",
     "regulatory", "监管执法/诉讼"),
    (r"\b(insolvent|insolvency|bankruptcy|halt(s|ed)? withdrawals?|suspend(s|ed)? withdrawals?)\b",
     "solvency", "偿付/提现风险"),
]

_COMPILED = [(re.compile(p, re.I), kind, label) for p, kind, label in _HARD_EVENT_RULES]


def classify_event(title: str) -> dict | None:
    """标题 → 硬否决事件类型。无命中返 None。纯函数，可离线测。"""
    if not title:
        return None
    for pattern, kind, label in _COMPILED:
        if pattern.search(title):
            return {"event": kind, "label": label}
    return None


def mentions_asset(text: str, base_asset: str, *, aliases: list[str] | None = None) -> bool:
    """标题/正文是否在说这个币。短 ticker（≤3 字符）只认括号形式，防误伤。纯函数。

    Args:
        aliases: 币名别名（"Bitcoin"/"Ethereum"）。**媒体新闻几乎只写币名不写 ticker**
            （标题是 "Bitcoin rallies" 不是 "(BTC) rallies"），不给别名的话 BTC 这类
            主流币的新闻一条都匹配不上。⚠️ 只给**情绪聚合**用；硬否决保持 ticker 严格，
            避免 "Bitcoin ETF 报道里出现 hack 字样" 这种误否决。

    ⛔ **只认交易对的基础腿**（`ALPACA/BTC` 说的是 ALPACA，不是 BTC）。此前把 `/BTC`
    也当成提及 BTC，于是币安每月一次的「Will Remove ALPACA/BTC, WING/ETH 交易对」例行公告
    就把 BTC 和 ETH 自己硬否决掉，还粘 14 天回看窗口——Jason 最常交易的两个币直接被打成回避。

    >>> mentions_asset("Notice of Removal of Spot Trading Pairs (OP/USDT)", "OP")
    True
    >>> mentions_asset("Binance opens new options market", "OP")
    False
    >>> mentions_asset("Binance Will Remove ALPACA/BTC and WING/ETH Spot Trading Pairs", "BTC")
    False
    >>> mentions_asset("Binance Will Remove ALPACA/BTC and WING/ETH Spot Trading Pairs", "ALPACA")
    True
    """
    if not text or not base_asset:
        return False
    t = base_asset.upper()
    # 括号形式 (OP) / (OP/USDT)：币名紧跟开括号 = 它就是被说的那个币
    if re.search(rf"[(\[]{re.escape(t)}\b", text, re.I):
        return True
    # 交易对**基础腿** OP/USDT（币名在斜杠左边）。斜杠右边是计价腿，不算提及该币
    if re.search(rf"(?<![A-Z0-9]){re.escape(t)}/", text, re.I):
        return True
    if re.search(rf"\b{re.escape(t)}(USDT|USDC|BUSD|BTC|ETH)\b", text):
        return True
    for alias in (aliases or []):
        if alias and re.search(rf"\b{re.escape(alias)}\b", text, re.I):
            return True
    if len(t) <= 3:
        return False        # 短 ticker 不做裸词匹配（OP/ID/SUI 误伤率太高）
    return bool(re.search(rf"\b{re.escape(t)}\b", text))


# 主流计价币：某个币「对 USDT 的交易对被摘掉」才等于它真的被下架；
# 只摘掉它对 BIDR/AEUR/TUSD 这类小众计价币的交易对，是**币安退役那个计价币**，与该币无关。
_MAIN_QUOTES = frozenset({"USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB"})
_PAIR_RE = re.compile(r"\b([A-Z0-9]{2,15})/([A-Z0-9]{2,15})\b")


def is_asset_delisted(title: str, base_asset: str) -> bool:
    """下架公告里，**这个币本身**是不是真的要被下架（而不是某个交易对被退役）。

    ⛔ 修的是一条会天天误伤主力币的判断。币安每隔一阵就发一次
    `Binance Will Remove BTC/BIDR, ETH/BIDR and USDT/BIDR Spot Trading Pairs`
    —— 这是**退役 BIDR 这个计价币**，BTC/ETH 屁事没有。但按「基础腿算提及」的规则，
    BTC 和 ETH 双双命中 delisting → `screen.verdict='avoid'` → composite 被压到 44 →
    出 SELL、目标仓位 0，而且 `lookback_days=14` 让这个误判**粘 14 天**。
    Jason 最常交易的两个币直接被打成回避。

    判据：币安要下架一个币时，**它对主流计价币的交易对一定在名单里**（尤其 /USDT）。
    所以：
      - 该币出现在 `XXX/主流计价币` 里 → 真下架；
      - 该币**只**出现在 `XXX/小众计价币` 里 → 只是那个计价币被退役，不否决；
      - 该币以非交易对形式被点名（`Will Delist XXX` / `(XXX)`）→ 真下架。

    >>> is_asset_delisted("Binance Will Remove BTC/BIDR, ETH/BIDR Spot Trading Pairs", "BTC")
    False
    >>> is_asset_delisted("Binance Will Delist XVG/USDT, XVG/BTC Spot Trading Pairs", "XVG")
    True
    >>> is_asset_delisted("Binance Will Delist ALPACA", "ALPACA")
    True
    """
    if not title or not base_asset:
        return False
    t = base_asset.upper()
    pairs = [(b.upper(), q.upper()) for b, q in _PAIR_RE.findall(title.upper())]
    as_base = [q for b, q in pairs if b == t]
    if as_base:
        # 出现在交易对里：只有搭主流计价币才算这个币真要被下架
        return any(q in _MAIN_QUOTES for q in as_base)
    # 没以交易对形式出现 → 看是不是被直接点名（此时 mentions_asset 已确认提到了它）
    #（作为计价腿出现不算：`ALPACA/BTC` 说的是 ALPACA 不是 BTC）
    return not any(q == t for _, q in pairs)


def asset_aliases(symbol: str) -> list[str]:
    """币的常见书面名（供情绪匹配）。取自策展的 CoinGecko id：`bitcoin` → "Bitcoin"。

    只对策展主流币给别名（表外的币走 ticker 匹配），因为 CoinGecko id 里
    `ethereum-classic` 这类带连字符的名字拆开后会和母币撞车，主流表是人工核过的。
    """
    from crypto_intel_engine.resolver import _CURATED, base_asset

    cid = _CURATED.get(base_asset(symbol).upper())
    if not cid:
        return []
    # `the-open-network` → "the open network"；单段名（bitcoin/solana）最有用
    name = cid.replace("-", " ")
    aliases = [name]
    if name.endswith(" 2"):          # avalanche-2 → avalanche
        aliases.append(name[:-2].strip())
    return [a for a in aliases if len(a) >= 4]


def recent_announcements(page_size: int = 20) -> list[dict] | None:
    """近期币安公告（**带 600s 进程内缓存**）。取数失败返 None（区别于「真的没公告」= []）。

    公告是**市场级**数据，与 symbol 无关。此前每个币每次分析各抓一次：10 币策略每 30 分钟
    就打 10 次币安 CMS，正是把自己打到限流、进而触发否决 fail-open 的根源。复用
    `context._cached` 的 TTL 缓存后一轮只打一次（失败不缓存，下轮还会重试）。
    """
    from acquisition.markets.crypto_news import fetch_binance_announcements
    from crypto_intel_engine.context import _cached
    return _cached(f"announcements_{page_size}",
                   lambda: fetch_binance_announcements(page_size=page_size))


def check_hard_events(symbol: str, announcements: list[dict] | None = None,
                      *, lookback_days: int = 14) -> dict:
    """这个币近期有没有硬否决级事件。返回 {veto, reasons, events, checked}。

    只看币安公告（权威、结构化）；媒体新闻噪声大，只进情绪维不做否决 —— 否则一条
    "XX 被黑客攻击" 的旧闻转载就能把好币误杀。

    ⚠️ `checked=False` 表示**这道闸根本没跑**（公告源不可用），不是「查过了没问题」。
    上层必须把这个区别透出去：否则一个昨天刚被宣布下架的币会被照常打分、照常推荐买入，
    而卡片上一个字都不提「宁可错过，不可踩雷」这道闸没执行。
    """
    from crypto_intel_engine.resolver import base_asset

    base = base_asset(symbol)
    result: dict[str, Any] = {"veto": False, "reasons": [], "events": [], "checked": True}
    if announcements is None:
        announcements = recent_announcements(page_size=20)
        if announcements is None:
            logger.warning(f"公告取数失败，事件否决未执行 {symbol}")
            result["checked"] = False
            return result

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    for ann in announcements or []:
        title = ann.get("title") or ""
        pub = ann.get("published_at")
        if pub is not None and pub < cutoff:
            continue
        if not mentions_asset(title, base):
            continue
        ev = classify_event(title)
        if not ev:
            continue
        # 下架类要再过一道「这个币本身是不是真被下架」的闸：币安退役小众计价币
        # （BTC/BIDR、ETH/AEUR…）是例行操作，不该把 BTC/ETH 自己否决掉 14 天
        if ev["event"] == "delisting" and not is_asset_delisted(title, base):
            logger.debug(f"{base} 出现在下架公告但只是交易对退役，不否决：{title[:80]}")
            continue
        result["veto"] = True
        result["reasons"].append(f"{ev['label']}：{title[:80]}")
        result["events"].append({**ev, "title": title, "url": ann.get("url"),
                                 "published_at": pub.isoformat() if pub else None})
    return result


# ──────────────────── 情绪聚合 ────────────────────


def aggregate_sentiment(symbol: str, *, days: int = 7) -> dict:
    """按币聚合 FinBERT 情绪 → {net_sentiment(-1..1), article_count, positive/negative/neutral}。

    net = (正面数 − 负面数) / 总数，按置信度加权。中性不计入分子（它不表达方向）。
    """
    from crypto_intel_engine.resolver import base_asset
    from data_engine.storage.database import get_session
    from data_engine.storage.models import NewsArticle, NewsSentiment

    base = base_asset(symbol)
    aliases = asset_aliases(symbol)      # 媒体写「Bitcoin」不写「BTC」，没别名会全都匹配不上
    # ⛔ crypto 新闻的 `published_at` 落的是 **UTC**（`crypto_news._ms_to_dt` 给的是
    # aware UTC，写库时 offset 被剥掉）——实测库里 crypto 行是 04:23、A股行是 15:21，
    # 同一列两种口径。这里按 UTC 切才对得上 crypto 那批。
    since = utc_now() - timedelta(days=days)
    session = get_session()
    try:
        rows = (session.query(NewsArticle, NewsSentiment)
                .join(NewsSentiment, NewsArticle.article_id == NewsSentiment.article_id)
                .filter(NewsArticle.market == "crypto")
                .filter(NewsArticle.published_at >= since)
                .all())
    except Exception as e:  # noqa: BLE001 — 表不存在/查询失败按无数据处理
        logger.warning(f"情绪聚合查询失败 {symbol}: {e}")
        return {"net_sentiment": None, "article_count": 0}
    finally:
        session.close()

    pos = neg = neu = 0
    weighted = 0.0
    for art, sent in rows:
        text = f"{art.title or ''} {art.summary or ''}"
        if not mentions_asset(text, base, aliases=aliases):
            continue
        conf = float(sent.confidence or 0)
        if sent.sentiment == "positive":
            pos += 1
            weighted += conf
        elif sent.sentiment == "negative":
            neg += 1
            weighted -= conf
        else:
            neu += 1

    total = pos + neg + neu
    if total == 0:
        return {"net_sentiment": None, "article_count": 0}
    return {
        "net_sentiment": round(weighted / total, 4),
        "article_count": total,
        "positive": pos, "negative": neg, "neutral": neu,
    }


# ──────────────────── 入库 ────────────────────


def _article_id(source: str, url: str) -> str:
    """与 `news_engine.fetcher._make_article_id` 同口径（MD5(source:url)），跨源去重一致。"""
    return hashlib.md5(f"{source}:{url}".encode()).hexdigest()


def ingest(*, announcement_pages: int = 20, analyze: bool = True) -> dict:
    """抓公告 + RSS → `NewsArticle(market='crypto')`，可选跑 FinBERT 情感。

    去重靠 `article_id` 唯一键（撞了就跳过，不更新——新闻正文不会变）。
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from acquisition.markets.crypto_news import (
        fetch_binance_announcements,
        fetch_crypto_news,
    )
    from data_engine.storage.database import get_session
    from data_engine.storage.models import NewsArticle

    items: list[dict] = []
    try:
        # 取数失败返 None（≠ 没有公告），入库侧按空处理即可——漏抓一轮下轮会补
        for ann in (fetch_binance_announcements(page_size=announcement_pages) or []):
            if not ann.get("url"):
                continue
            items.append({
                "source": f"Binance/{ann.get('catalog_name')}",
                "title": ann["title"], "url": ann["url"],
                "summary": None, "published_at": ann.get("published_at"),
            })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"公告抓取失败: {e}")
    try:
        items.extend(fetch_crypto_news())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"新闻抓取失败: {e}")

    if not items:
        return {"fetched": 0, "inserted": 0, "analyzed": 0}

    session = get_session()
    new_ids: list[str] = []
    try:
        for it in items:
            aid = _article_id(it["source"], it["url"])
            pub = it.get("published_at")
            stmt = sqlite_insert(NewsArticle).values(
                article_id=aid, symbol=None, market="crypto",
                title=it["title"][:500], content=None,
                summary=(it.get("summary") or None),
                source=it["source"][:100], url=it["url"][:1000],
                language="en",
                published_at=pub.replace(tzinfo=None) if pub else None,
            ).on_conflict_do_nothing(index_elements=["article_id"])
            res = session.execute(stmt)
            if res.rowcount:
                new_ids.append(aid)
        session.commit()
    except Exception as e:  # noqa: BLE001
        session.rollback()
        logger.warning(f"新闻入库失败: {e}")
    finally:
        session.close()

    analyzed = 0
    if analyze and new_ids:
        try:
            from news_engine.sentiment import SentimentAnalyzer
            results = SentimentAnalyzer.get_instance().analyze_batch(new_ids)
            analyzed = len(results)
        except Exception as e:  # noqa: BLE001 — 情感模型挂了不该拖垮抓取
            logger.warning(f"crypto 新闻情感分析失败: {e}")

    logger.info(f"[crypto] 新闻入库：抓 {len(items)} 条，新增 {len(new_ids)}，情感 {analyzed}")
    return {"fetched": len(items), "inserted": len(new_ids), "analyzed": analyzed}
