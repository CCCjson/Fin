"""
新闻分析 API — 抓取 + BERT 情感 + OpenAI 深度分析
"""
import asyncio
import json
from typing import Literal, Optional, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.routes._stream_utils import bridge_sync_stream
from news_engine.articles import list_articles_scored, list_unanalyzed_article_ids
from news_engine.fetcher import NewsFetcher
from news_engine.sentiment import SentimentAnalyzer
from news_engine.analyzer import NewsAnalyzer
from llm_config import get_cheap_model

router = APIRouter(prefix="/news", tags=["新闻分析"])

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
            # 桥接为 daemon 线程跑，避免抓取的阻塞式网络 IO 占住事件循环
            async for event_line in bridge_sync_stream(fetch_gen):
                yield event_line
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

            article_ids = await asyncio.to_thread(
                list_unanalyzed_article_ids, request.symbol, request.market)

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
    try:
        return await asyncio.to_thread(
            list_articles_scored,
            symbol=symbol, market=market, sentiment=sentiment,
            limit=limit, offset=offset, sort=sort,
        )
    except Exception as e:
        logger.error(f"查询新闻失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


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
        bridge_sync_stream(sync_gen),
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
    from news_engine.fetcher import NewsFetcher

    loop = asyncio.get_event_loop()

    try:
        fetcher = NewsFetcher()
        all_news = await loop.run_in_executor(
            None, fetcher.collect_market_news
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
                None, lambda: NewsFetcher().search_global_headlines(queries)
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
        bridge_sync_stream(sync_gen),
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
