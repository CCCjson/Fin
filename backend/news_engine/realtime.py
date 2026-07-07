"""
无状态实时新闻情绪服务 —— 抓取 → 内存 BERT 打分 → 聚合，不入库。

供 MoneyBill 直接工具 / news 子智能体 / cockpit 情绪维度三方共用。
与前端 News 页面那套「抓取即入库」的流程并行，互不干扰。
"""
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from loguru import logger

from news_engine.fetcher import NewsFetcher
from news_engine.sentiment import SentimentAnalyzer

# 进程内 TTL 缓存：避免同一轮对话里多个工具重复抓同一只股票。
# key = raw_symbol，value = (存入时间戳, 结果 dict)。纯内存，进程重启即失效。
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 600  # 10 分钟

_LABEL_CN = {"positive": "偏多", "negative": "偏空", "neutral": "中性"}


def _empty(reason: str, article_count: int = 0) -> Dict:
    return {
        "available": False,
        "reason": reason,
        "score": None,
        "label": None,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "total": 0,
        "article_count": article_count,
        "articles": [],
    }


def get_realtime_sentiment(
    symbol: str, days: int = 7, limit: int = 30, market: str = "a_share",
) -> Dict:
    """实时抓取个股新闻并做情绪聚合打分（不入库）。

    Args:
        symbol: 股票代码，如 '300059'/'300059.SZ'（A股，内部取纯数字）、
            '00700.HK'（港股）、'AAPL'（美股，不带后缀）。
        days: 只统计近 N 日新闻。
        limit: 最多分析的新闻条数（取最新的）。
        market: a_share / hk_stock / us_stock，决定走哪个数据源。

    Returns:
        {
            available, reason,
            score,            # 0-100，50=中性；口径与 cockpit 一致
            label,            # 偏多 / 偏空 / 中性
            positive, negative, neutral, total,
            article_count,    # 抓到的原始条数（未按 days 过滤前）
            articles: [{title, source, published_at, sentiment}, ...]
        }
    不抛异常：抓取/打分失败一律返回 available=False。
    """
    raw_symbol = symbol.split(".")[0].strip()
    if not raw_symbol:
        return _empty("空的股票代码")

    # ---- TTL 缓存命中（key 带 market，避免跨市场同代码撞缓存）----
    cache_key = f"{market}:{raw_symbol}:{days}:{limit}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        logger.debug(f"实时情绪缓存命中: {market}:{raw_symbol}")
        return hit[1]

    # ---- 抓取（复用 NewsFetcher，返回 List[Dict]，本就不入库）----
    try:
        fetcher = NewsFetcher()
        if market == "hk_stock":
            raw_articles: List[Dict] = fetcher.fetch_hk_stock_news(raw_symbol)
        elif market == "us_stock":
            raw_articles = fetcher.fetch_us_stock_news(raw_symbol, days=days)
        else:
            raw_articles = fetcher.fetch_a_share_news(raw_symbol)
    except Exception as e:  # noqa: BLE001 — 抓取任何异常都降级，不阻断上层
        logger.warning(f"实时新闻抓取失败 {market}:{raw_symbol}: {e}")
        return _empty(f"新闻抓取失败: {e}")

    if not raw_articles:
        return _empty("近期无相关新闻", article_count=0)

    # ---- 按 days 过滤 + 取最新 limit 条 ----
    since = datetime.now() - timedelta(days=days)
    recent: List[Dict] = []
    for art in raw_articles:
        pub = art.get("published_at")
        if isinstance(pub, datetime) and pub < since:
            continue
        recent.append(art)
    recent.sort(
        key=lambda a: a.get("published_at") or datetime.min, reverse=True
    )
    recent = recent[:limit]

    if not recent:
        return _empty(f"近 {days} 日无相关新闻", article_count=len(raw_articles))

    # ---- 内存逐条 BERT 打分（analyze 单条、不落库）----
    analyzer = SentimentAnalyzer.get_instance()
    pos = neg = neu = 0
    scored: List[Dict] = []
    for art in recent:
        text = (art.get("title") or "").strip()
        if art.get("content"):
            text += " " + art["content"][:300]
        try:
            res = analyzer.analyze(text.strip(), language=art.get("language") or "zh")
            sentiment = res.get("sentiment", "neutral")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"单条情绪分析失败，记为中性: {e}")
            sentiment = "neutral"

        if sentiment == "positive":
            pos += 1
        elif sentiment == "negative":
            neg += 1
        else:
            neu += 1

        pub = art.get("published_at")
        scored.append({
            "title": art.get("title"),
            "source": art.get("source"),
            "published_at": pub.strftime("%Y-%m-%d %H:%M") if isinstance(pub, datetime) else str(pub),
            "sentiment": sentiment,
        })

    total = pos + neg + neu
    if total == 0:
        return _empty("无有效情绪结果", article_count=len(raw_articles))

    # ---- 聚合打分：口径与 cockpit._score_sentiment 完全一致 ----
    score = round(max(0.0, min(100.0, 50 + 50 * (pos - neg) / total)), 1)
    label = "偏多" if score > 55 else ("偏空" if score < 45 else "中性")

    result = {
        "available": True,
        "reason": "",
        "score": score,
        "label": label,
        "positive": pos,
        "negative": neg,
        "neutral": neu,
        "total": total,
        "article_count": len(raw_articles),
        "articles": scored,
    }

    _CACHE[cache_key] = (time.time(), result)
    logger.info(f"实时情绪 {raw_symbol}: score={score} (+{pos}/-{neg}/={neu})")
    return result
