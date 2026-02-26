"""
新闻分析 API — 抓取 + BERT 情感 + OpenAI 深度分析
"""
import asyncio
import json
import queue
import threading
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from data_engine.storage.database import get_session
from data_engine.storage.models import NewsArticle, NewsSentiment
from news_engine.fetcher import NewsFetcher
from news_engine.sentiment import SentimentAnalyzer
from news_engine.analyzer import NewsAnalyzer

router = APIRouter(prefix="/news", tags=["新闻分析"])

_fetcher = NewsFetcher()
_analyzer = NewsAnalyzer()


# ==================== 请求模型 ====================

class FetchRequest(BaseModel):
    symbol: Optional[str] = Field(None, description="股票代码，如 300059")
    market: str = Field("a_share", description="a_share / general")


class AnalyzeRequest(BaseModel):
    article_id: str = Field(..., description="文章 ID")
    model: str = Field("gpt-4o", description="模型: gpt-4o / gpt-4o-mini 等")


class ReportRequest(BaseModel):
    symbol: Optional[str] = Field(None, description="股票代码")
    market: str = Field("a_share", description="a_share / general")
    model: str = Field("gpt-4o", description="模型")


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
            articles.append(item)

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
