"""
新闻/舆情类工具 —— 无状态实时抓取 + BERT 情绪打分 + 盘前聚合简报。

复用 news_engine.realtime.get_realtime_sentiment（不入库）与
report_engine.web_searcher.MarketWebSearcher（同 /news/morning-briefing）。
"""
from typing import Literal

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope


class GetNewsSentimentArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH、00700.HK、AAPL")
    days: int = Field(7, ge=1, le=90, description="只统计近 N 日新闻，默认 7")
    market: Literal["a_share", "hk_stock", "us_stock"] = Field(
        "a_share", description="市场，默认 a_share；港股/美股用 hk_stock/us_stock")


@tool(
    name="get_news_sentiment",
    description=(
        "获取单只股票（A股/港股/美股）近期新闻的情绪分数(0-100，50=中性)、"
        "利好/利空/中性条数及最新标题。实时抓取、秒级返回结构化分数，"
        "适合快速判断某股舆情偏多还是偏空、有没有突发消息。"
        "如需 AI 深度解读新闻含义与影响（更慢更全），改用 run_news_analysis。"
    ),
    args_model=GetNewsSentimentArgs,
    category="analysis",
    group="news",
)
def get_news_sentiment(symbol: str, days: int = 7, market: str = "a_share") -> ToolEnvelope:
    from news_engine.realtime import get_realtime_sentiment
    from agents.widgets import metric_cards_widget

    r = get_realtime_sentiment(symbol, days=days, market=market)
    if not r["available"]:
        return ToolEnvelope(business_result="negative",
                             message=f"{symbol} 近 {days} 日无可用新闻情绪数据（{r.get('reason', '数据不足')}）。")

    headlines = [
        {"title": a["title"], "source": a["source"],
         "published_at": a["published_at"], "sentiment": a["sentiment"]}
        for a in r["articles"][:8]
    ]
    summary = {
        "symbol": symbol,
        "score": r["score"],
        "label": r["label"],
        "positive": r["positive"],
        "negative": r["negative"],
        "neutral": r["neutral"],
        "total": r["total"],
        "days": days,
        "headlines": headlines,
    }

    cards = [
        {"label": "情绪分", "value": r["score"], "type": "quality",
         "positive": r["score"] >= 50},
        {"label": "利好", "value": r["positive"], "type": "return", "positive": True},
        {"label": "利空", "value": r["negative"], "type": "risk", "positive": False},
        {"label": "中性", "value": r["neutral"], "type": "neutral"},
    ]
    widget = metric_cards_widget(cards, title=f"{symbol} 新闻情绪（近{days}日 {r['total']}条）")
    return ToolEnvelope(data=summary, widget=widget)


class GetMorningBriefArgs(BaseModel):
    limit_per_source: int = Field(8, ge=3, le=20, description="每路新闻最多条数，默认 8")


@tool(
    name="get_morning_brief",
    description=(
        "盘前新闻简报：聚合隔夜国内财经、全球/国际、英文金融（Finnhub）三路新闻头条。"
        "开盘前问「隔夜有什么消息/来个盘前简报」时调用。返回原始头条列表；"
        "如需 AI 综合解读市场影响，拿到头条后再用 run_news_analysis。"
    ),
    args_model=GetMorningBriefArgs,
    category="analysis",
    group="news",
)
def get_morning_brief(limit_per_source: int = 8) -> ToolEnvelope:
    from report_engine.web_searcher import MarketWebSearcher

    lim = max(3, min(int(limit_per_source or 8), 20))
    searcher = MarketWebSearcher(random_ip=False)
    all_news = searcher._collect_all_news(None)
    if not all_news:
        return ToolEnvelope(business_result="negative", message="暂时没抓到隔夜新闻，稍后再试。")

    def _pick(category: str) -> list[dict]:
        return [
            {"title": n.get("title"), "source": n.get("source"),
             "published_at": n.get("published_at") or n.get("time")}
            for n in all_news if n.get("category") == category
        ][:lim]

    summary = {
        "total": len(all_news),
        "domestic": _pick("domestic"),
        "global": _pick("global"),
        "finnhub": _pick("finnhub"),
    }
    return ToolEnvelope(data=summary)


class GetRecentNewsConclusionsArgs(BaseModel):
    symbol: str = Field(
        "", description="可选：只看某只股票的结论，不填则看大盘综合结论")
    limit: int = Field(5, ge=1, le=20, description="返回条数，默认 5")


@tool(
    name="get_recent_news_conclusions",
    description=(
        "查询新闻定时任务已经生成并落库的结论（AI 综合解读，非实时抓取），"
        "秒回、不重新触发抓取。不填 symbol 看大盘综合结论，填 symbol 看该股结论。"
        "用户问「最近新闻怎么看/新闻任务分析出啥了」时用；要重新实时抓取解读用 run_news_analysis。"
    ),
    args_model=GetRecentNewsConclusionsArgs,
    category="analysis",
    group="news",
)
def get_recent_news_conclusions(symbol: str = "", limit: int = 5) -> ToolEnvelope:
    from data_engine.storage.database import get_session
    from data_engine.storage.models import NewsAnalysis

    session = get_session()
    try:
        query = session.query(NewsAnalysis)
        if symbol:
            query = query.filter(NewsAnalysis.symbol == symbol.split(".")[0])
        else:
            query = query.filter(NewsAnalysis.symbol.is_(None))
        rows = (
            query.filter(NewsAnalysis.status == "completed")
            .order_by(NewsAnalysis.created_at.desc())
            .limit(limit)
            .all()
        )
        items = [
            {
                "analysis_id": r.analysis_id,
                "symbol": r.symbol,
                "content": r.content,
                "created_at": str(r.created_at) if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()

    if not items:
        return ToolEnvelope(
            business_result="negative",
            message=f"暂无{('「' + symbol + '」') if symbol else '大盘'}的结论，新闻定时任务可能还没跑到。")
    return ToolEnvelope(data={"count": len(items), "conclusions": items})
