"""
已缓存新闻的查询/打分/去重 —— 从 api/routes/news.py 下沉（域9 厚 route 下沉）。

route 只做 HTTP 编解码，重要度打分公式（Jason 确认过的权重）、SQLAlchemy join 查询、
按重要度排序、按标题去重全在这里。/fetch 流程里「查未分析文章」也归口到这里。
"""
from datetime import datetime

from data_engine.storage.database import get_session
from data_engine.storage.models import NewsArticle, NewsSentiment
from news_engine.keywords import load_high_impact_keywords, match_keyword

# ==================== sort=importance 打分公式（Jason 确认过的权重）====================
# 类别：命中关键词类别加分，geopolitical 权重最高（地缘政治有时候比财经消息影响力更大）
_CATEGORY_SCORE = {"geopolitical": 1.0, "financial_risk": 0.8, "urgent": 0.8}
_IMPORTANCE_WEIGHT = 0.65
_RECENCY_WEIGHT = 0.35
_RECENCY_HALFLIFE_HOURS = 6.0  # 时效性分数每 6 小时衰减一半


def _importance_score(article: NewsArticle, sent: NewsSentiment | None, category: str | None) -> float:
    """三项等权平均：命中关键词类别 + 情绪强度(非中性) + 自选股/持仓相关性。"""
    category_score = _CATEGORY_SCORE.get(category, 0.0)
    sentiment_score = sent.confidence if (sent and sent.sentiment != "neutral" and sent.confidence) else 0.0
    symbol_score = 1.0 if article.symbol else 0.0
    return (category_score + sentiment_score + symbol_score) / 3


def _recency_score(published_at: datetime | None) -> float:
    """连续指数衰减，半衰期 6 小时。"""
    if not published_at:
        return 0.0
    age_hours = max(0.0, (datetime.now() - published_at).total_seconds() / 3600)
    return 0.5 ** (age_hours / _RECENCY_HALFLIFE_HOURS)


def list_articles_scored(
    symbol: str | None = None,
    market: str | None = None,
    sentiment: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort: str = "recent",
) -> dict:
    """查询已缓存新闻（含 BERT 情感），可按重要度打分排序 + 按标题去重。

    返回 {total, articles[]}——total 为去重前的匹配行数，articles 为本页去重后的结果。
    同步落库调用，供 route 经 asyncio.to_thread 驱动。
    """
    session = get_session()
    try:
        query = session.query(NewsArticle, NewsSentiment).outerjoin(
            NewsSentiment, NewsArticle.article_id == NewsSentiment.article_id
        )

        if symbol:
            raw_symbol = symbol.split(".")[0]
            query = query.filter(NewsArticle.symbol == raw_symbol)
        if market:
            query = query.filter(NewsArticle.market == market)
        if sentiment:
            query = query.filter(NewsSentiment.sentiment == sentiment)

        total = query.count()

        rows = (
            query.order_by(NewsArticle.published_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        keywords_by_category = {}
        if sort == "importance":
            keywords_by_category = load_high_impact_keywords()

        articles = []
        for article, sent in rows:
            item = {
                "article_id": article.article_id,
                "symbol": article.symbol,
                "market": article.market,
                "title": article.title,
                "content": article.content,
                "summary": article.summary,
                "source": article.source,
                "url": article.url,
                "image_url": article.image_url,
                "language": article.language,
                "published_at": str(article.published_at) if article.published_at else None,
                "fetched_at": str(article.fetched_at) if article.fetched_at else None,
            }
            if sent:
                item["sentiment"] = {
                    "sentiment": sent.sentiment,
                    "confidence": sent.confidence,
                    "prob_positive": sent.prob_positive,
                    "prob_negative": sent.prob_negative,
                    "prob_neutral": sent.prob_neutral,
                    "model_used": sent.model_used,
                }
            else:
                item["sentiment"] = None

            if sort == "importance":
                matched = match_keyword(article.title or "", keywords_by_category)
                category = matched[0] if matched else None
                item["category"] = category
                item["score"] = round(
                    _IMPORTANCE_WEIGHT * _importance_score(article, sent, category)
                    + _RECENCY_WEIGHT * _recency_score(article.published_at),
                    2,
                )
            articles.append(item)

        if sort == "importance":
            articles.sort(key=lambda a: a.get("score", 0.0), reverse=True)

        # 按标题去重（保留排序后第一条，即分数/时间最优的那条）：修复 store_articles 之前
        # 就已经入库的历史重复数据——不同来源/栏目转载同一条新闻在库里各占一行，只对
        # 本页展示做兜底去重，不动 DB 也不影响 total（total 仍是去重前的匹配行数）。
        seen_titles = set()
        deduped = []
        for a in articles:
            if a["title"] in seen_titles:
                continue
            seen_titles.add(a["title"])
            deduped.append(a)

        return {"total": total, "articles": deduped}
    finally:
        session.close()


def list_unanalyzed_article_ids(symbol: str | None = None, market: str | None = None) -> list[str]:
    """查还没跑过 BERT 情感的文章 id（/fetch 流程 step 2）。同步落库调用。"""
    session = get_session()
    try:
        query = session.query(NewsArticle).outerjoin(
            NewsSentiment,
            NewsArticle.article_id == NewsSentiment.article_id,
        ).filter(NewsSentiment.id.is_(None))

        if symbol:
            raw_symbol = symbol.split(".")[0]
            query = query.filter(NewsArticle.symbol == raw_symbol)
        if market:
            query = query.filter(NewsArticle.market == market)

        return [a.article_id for a in query.all()]
    finally:
        session.close()
