"""
新闻分析 API — 抓取 + BERT 情感 + OpenAI 深度分析
"""
import asyncio
import json
import queue
import threading
from datetime import datetime
from typing import Literal, Optional, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from data_engine.storage.database import get_session
from data_engine.storage.models import NewsArticle, NewsSentiment
from news_engine.fetcher import NewsFetcher
from news_engine.sentiment import SentimentAnalyzer
from news_engine.analyzer import NewsAnalyzer
from llm_config import get_cheap_model

router = APIRouter(prefix="/news", tags=["新闻分析"])

# ==================== sort=importance 打分公式（Jason 确认过的权重）====================
# 类别：命中关键词类别加分，geopolitical 权重最高（地缘政治有时候比财经消息影响力更大）
_CATEGORY_SCORE = {"geopolitical": 1.0, "financial_risk": 0.8, "urgent": 0.8}
_IMPORTANCE_WEIGHT = 0.65
_RECENCY_WEIGHT = 0.35
_RECENCY_HALFLIFE_HOURS = 6.0  # 时效性分数每 6 小时衰减一半


def _importance_score(article: NewsArticle, sent: Optional[NewsSentiment], category: Optional[str]) -> float:
    """三项等权平均：命中关键词类别 + 情绪强度(非中性) + 自选股/持仓相关性。"""
    category_score = _CATEGORY_SCORE.get(category, 0.0)
    sentiment_score = sent.confidence if (sent and sent.sentiment != "neutral" and sent.confidence) else 0.0
    symbol_score = 1.0 if article.symbol else 0.0
    return (category_score + sentiment_score + symbol_score) / 3


def _recency_score(published_at: Optional[datetime]) -> float:
    """连续指数衰减，半衰期 6 小时。"""
    if not published_at:
        return 0.0
    age_hours = max(0.0, (datetime.now() - published_at).total_seconds() / 3600)
    return 0.5 ** (age_hours / _RECENCY_HALFLIFE_HOURS)

_fetcher = NewsFetcher()
_analyzer = NewsAnalyzer()


# ==================== 请求模型 ====================

class FetchRequest(BaseModel):
    symbol: Optional[str] = Field(None, description="股票代码，如 300059")
    market: str = Field("a_share", description="a_share / general")


class AnalyzeRequest(BaseModel):
    article_id: str = Field(..., description="文章 ID")
    model: str = Field(default_factory=get_cheap_model, description="模型，默认便宜档")


class ReportRequest(BaseModel):
    symbol: Optional[str] = Field(None, description="股票代码")
    market: str = Field("a_share", description="a_share / general")
    model: str = Field(default_factory=get_cheap_model, description="模型，默认便宜档")


# ==================== 辅助函数 ====================

def _sync_gen_to_async(sync_gen):
    """将同步生成器桥接到 async 生成器（thread + queue）"""
    chunk_queue: queue.Queue = queue.Queue()
    sentinel = object()

    def _drain():
        try:
            for item in sync_gen:
                chunk_queue.put(item)
        except Exception as exc:
            chunk_queue.put(exc)
        finally:
            chunk_queue.put(sentinel)

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()

    async def _async_iter():
        while True:
            try:
                item = chunk_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue

            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item

            yield item
            await asyncio.sleep(0)

    return _async_iter()


# ==================== 端点 ====================

@router.post("/fetch", summary="抓取新闻 + BERT 情感分析（流式）")
async def fetch_news(request: FetchRequest):
    """
    抓取新闻并进行 BERT 情感分析。

    流式返回 NDJSON 事件:
    - fetching: 抓取进度
    - fetched: 抓取完成
    - analyzing: BERT 分析进度
    - complete: 全部完成
    - error: 出错
    """

    def _ndjson(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False) + "\n"

    async def _streaming():
        try:
            # Step 1: 抓取新闻
            fetch_gen = _fetcher.fetch_and_store(
                symbol=request.symbol,
                market=request.market,
            )

            fetched_ok = False
            for event_line in fetch_gen:
                yield event_line
                await asyncio.sleep(0)
                try:
                    evt = json.loads(event_line)
                    if evt.get("event") == "fetched":
                        fetched_ok = True
                except json.JSONDecodeError:
                    pass

            if not fetched_ok:
                return

            # Step 2: 查询需要分析的文章
            yield _ndjson({"event": "analyzing", "message": "正在进行 BERT 情感分析..."})
            await asyncio.sleep(0)

            session = get_session()
            try:
                query = session.query(NewsArticle).outerjoin(
                    NewsSentiment,
                    NewsArticle.article_id == NewsSentiment.article_id,
                ).filter(NewsSentiment.id.is_(None))

                if request.symbol:
                    raw_symbol = request.symbol.split(".")[0]
                    query = query.filter(NewsArticle.symbol == raw_symbol)
                if request.market:
                    query = query.filter(NewsArticle.market == request.market)

                unanalyzed = query.all()
                article_ids = [a.article_id for a in unanalyzed]
            finally:
                session.close()

            # Step 3: BERT 情感分析
            if article_ids:
                yield _ndjson({
                    "event": "analyzing",
                    "message": f"正在分析 {len(article_ids)} 条新闻的情感...",
                })
                await asyncio.sleep(0)

                # BERT 分析在子线程中运行（避免阻塞事件循环）
                loop = asyncio.get_event_loop()
                analyzer = SentimentAnalyzer.get_instance()

                try:
                    results = await loop.run_in_executor(
                        None, analyzer.analyze_batch, article_ids
                    )
                    yield _ndjson({
                        "event": "analyzing",
                        "message": f"情感分析完成: {len(results)} 条",
                    })
                except Exception as e:
                    logger.error(f"BERT 分析失败: {e}")
                    yield _ndjson({"event": "error", "message": f"情感分析失败: {e}"})
                    return
            else:
                yield _ndjson({"event": "analyzing", "message": "所有新闻已有情感分析结果"})

            await asyncio.sleep(0)

            # Step 4: 返回完成事件
            yield _ndjson({"event": "complete", "message": "新闻抓取和情感分析全部完成"})

        except Exception as e:
            logger.error(f"新闻抓取流式异常: {e}")
            yield _ndjson({"event": "error", "message": str(e)})

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/articles", summary="查询已缓存新闻（带情感）")
async def get_articles(
    symbol: Optional[str] = Query(None, description="股票代码"),
    market: Optional[str] = Query(None, description="a_share / general"),
    sentiment: Optional[str] = Query(None, description="positive / negative / neutral"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: Literal["recent", "importance"] = Query(
        "recent", description="recent=按时间倒序(默认) / importance=按重要度打分排序"),
):
    """查询已缓存的新闻列表，包含 BERT 情感分析结果。"""
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
        match_keyword = None
        if sort == "importance":
            from news_engine.news_scheduler import _load_high_impact_keywords, _match_keyword
            keywords_by_category = _load_high_impact_keywords()
            match_keyword = _match_keyword

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
        articles = deduped

        return {"total": total, "articles": articles}

    except Exception as e:
        logger.error(f"查询新闻失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.post("/analyze", summary="OpenAI 单篇新闻深度分析（流式）")
async def analyze_article(request: AnalyzeRequest):
    """
    对单篇新闻进行 OpenAI 深度分析。

    流式返回 NDJSON 事件: start → chunk → done | error
    """
    sync_gen = _analyzer.analyze_single_stream(
        article_id=request.article_id,
        model=request.model,
    )

    return StreamingResponse(
        _sync_gen_to_async(sync_gen),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class MorningBriefingRequest(BaseModel):
    """盘前预览请求"""
    limit_per_source: int = Field(15, description="每个新闻源最多返回条数")
    enable_web: bool = Field(False, description="是否额外用真·联网搜索补充全球头条")
    queries: Optional[List[str]] = Field(None, description="联网搜索词，不填用默认")


@router.post("/morning-briefing", summary="盘前新闻聚合（隔夜事件）")
async def morning_briefing(request: MorningBriefingRequest):
    """
    聚合隔夜国内外新闻（东财国内/全球/国际 + Finnhub），
    供盘前预览的交叉分析使用。

    返回:
    - domestic: 国内财经新闻
    - global: 全球/国际新闻
    - finnhub: 英文金融新闻
    - total: 总条数
    """
    from report_engine.web_searcher import MarketWebSearcher

    loop = asyncio.get_event_loop()

    try:
        searcher = MarketWebSearcher(random_ip=False)
        all_news = await loop.run_in_executor(
            None, searcher._collect_all_news, None
        )
    except Exception as e:
        logger.error(f"盘前新闻聚合失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

    # 按 category 分组
    domestic = [n for n in all_news if n.get("category") == "domestic"]
    global_news = [n for n in all_news if n.get("category") == "global"]
    finnhub = [n for n in all_news if n.get("category") == "finnhub"]

    # 可选：真·联网搜索补充全球头条（独立 try，失败不拖垮上面三组）
    web: list = []
    if request.enable_web:
        try:
            queries = request.queries or ["global financial markets today", "A股 市场 隔夜 外盘"]
            web = await loop.run_in_executor(
                None, lambda: MarketWebSearcher(random_ip=False).search_global_headlines(queries)
            )
        except Exception as e:
            logger.warning(f"联网头条补充失败，跳过: {e}")
            web = []

    # 限制条数
    lim = request.limit_per_source
    resp = {
        "domestic": domestic[:lim],
        "global": global_news[:lim],
        "finnhub": finnhub[:lim],
        "total": len(all_news),
    }
    if request.enable_web:
        resp["web"] = web[:lim]
    return resp


@router.post("/report", summary="OpenAI 综合新闻报告（流式）")
async def generate_report(request: ReportRequest):
    """
    基于已缓存新闻生成 OpenAI 综合分析报告。

    流式返回 NDJSON 事件: start → chunk → done | error
    """
    sync_gen = _analyzer.generate_report_stream(
        symbol=request.symbol,
        market=request.market,
        model=request.model,
    )

    return StreamingResponse(
        _sync_gen_to_async(sync_gen),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ==================== Newnew 定时任务（15分钟抓取+分析）状态/开关 ====================

class NewsJobToggleRequest(BaseModel):
    enabled: bool


@router.get("/job/status", summary="Newnew 定时新闻任务状态（运行/开关/下次运行/上轮结果）")
async def get_news_job_status():
    from news_engine.news_scheduler import news_scheduler
    return news_scheduler.get_status()


@router.post("/job/toggle", summary="开/关 Newnew 定时新闻任务（运行时，无需改 .env）")
async def toggle_news_job(request: NewsJobToggleRequest):
    try:
        from news_engine.news_scheduler import news_scheduler
        return news_scheduler.set_enabled(request.enabled)
    except Exception as e:  # noqa: BLE001
        logger.error(f"切换新闻定时任务开关失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/job/run-once", summary="手动立即跑一轮新闻抓取+分析（debug 用，不用等定时触发）")
async def run_news_job_once():
    try:
        from news_engine.news_scheduler import news_scheduler
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, news_scheduler.run_once_sync)
    except Exception as e:  # noqa: BLE001
        logger.error(f"手动触发新闻任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
