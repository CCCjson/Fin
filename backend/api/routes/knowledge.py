"""
外置金融大脑 knowledge_engine API —— 文档/检索/摄入/alpha ideas。

注意：摄入/提炼/回测是慢任务（DDG 限速 + LLM + 回测），同步返回，前端要给足超时
或放后台触发。批量摄入别频繁打。
"""
import json
import queue
import threading
from typing import Optional, List

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.routes._stream_utils import bridge_sync_stream

router = APIRouter(prefix="/knowledge", tags=["外置金融大脑"])


# ==================== 请求模型 ====================

class IngestPapersRequest(BaseModel):
    queries: List[str] = Field(..., description="arxiv 论文搜索词列表")
    max_docs: int = Field(10, description="最多摄入篇数")


class IngestWebRequest(BaseModel):
    queries: List[str] = Field(..., description="搜索词列表")
    source_type: str = Field("research", description="paper/research/financial/news")
    site: Optional[str] = Field(None, description="限定站点，如 arxiv.org")
    use_sec: bool = Field(False, description="走 SEC EDGAR 而非 DDG")
    max_docs: int = Field(10)


class IngestCninfoStartRequest(BaseModel):
    categories: Optional[List[str]] = Field(None, description="披露类别，默认 [年报,半年报]")
    per_category: int = Field(1, ge=1, le=20, description="每类取最近 N 份")
    max_pages: int = Field(60, ge=1, le=300, description="每份 PDF 抽前 N 页")
    start_date: str = Field("20240101", description="披露起始日期 YYYYMMDD")
    end_date: str = Field("20261231", description="披露截止日期 YYYYMMDD")


class IngestResearchReportStartRequest(BaseModel):
    limit_per_symbol: int = Field(60, ge=1, le=200, description="时间窗口过滤后再截断的安全阀")
    full_text: bool = Field(False, description="默认两阶段：False=仅元数据要点（快、省CPU），全市场跑完后按需走 upgrade_full_text 补全文；True=直接拉PDF全文入库（慢、吃CPU）")
    start_date: str = Field("20220101", description="研报发布起始日期 YYYYMMDD")
    end_date: str = Field("20261231", description="研报发布截止日期 YYYYMMDD")


class UpgradeResearchReportFullTextRequest(BaseModel):
    symbols: List[str] = Field(..., description="要升级为全文的股票代码列表（已存在仅元数据的文档才会处理）")


class IngestArxivRequest(BaseModel):
    categories: Optional[List[str]] = Field(None, description="arXiv分类，默认q-fin.*五个子类")
    keywords: Optional[List[str]] = Field(None, description="叠加摘要关键词过滤，默认量化交易相关词组")
    start_date: str = Field("20220101", description="论文提交起始日期 YYYYMMDD")
    end_date: str = Field("20261231", description="论文提交截止日期 YYYYMMDD")
    max_results: int = Field(300, ge=1, le=2000, description="最多摄入篇数")
    full_text: bool = Field(False, description="True=额外抓PDF全文，False=只用摘要")


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(5)
    source_type: str = Field("all")


class MineRequest(BaseModel):
    limit: int = Field(5, description="本次最多提炼几篇")


class BacktestRequest(BaseModel):
    data_start: str = Field(..., description="回测起始 YYYY-MM-DD")
    data_end: str = Field(..., description="回测结束 YYYY-MM-DD")
    symbols: Optional[List[str]] = Field(None, description="标的，不填用 idea 建议或默认")
    max_iterations: int = Field(8, ge=1, le=20)


class ScrapeStreamRequest(BaseModel):
    url: str = Field(..., description="目标网页完整 URL 或域名")
    want: Optional[str] = Field(None, description="想要什么数据的自然语言描述")
    endpoint: Optional[str] = Field(None, description="可选，精确指定接口名")
    params: Optional[dict] = Field(None, description="可选，查询参数覆盖")


# ==================== 文档 / 检索 ====================

@router.get("/stats", summary="知识库统计")
async def stats():
    from knowledge_engine.store import KnowledgeStore
    from knowledge_engine import vector_store
    s = KnowledgeStore().stats()
    s["vectors"] = vector_store.count()
    return s


@router.get("/documents", summary="文档列表")
async def list_documents(source_type: Optional[str] = None, limit: int = 100, offset: int = 0):
    from knowledge_engine.store import KnowledgeStore
    docs = KnowledgeStore().list_documents(source_type=source_type, limit=limit, offset=offset)
    return [{
        "doc_id": d.doc_id, "source_type": d.source_type, "title": d.title,
        "url": d.url, "language": d.language, "status": d.status,
        "chunk_count": d.chunk_count,
        "published_at": d.published_at.isoformat() if d.published_at else None,
        "fetched_at": d.fetched_at.isoformat() if d.fetched_at else None,
    } for d in docs]


@router.post("/search", summary="语义检索（RAG）")
async def search(req: SearchRequest):
    from knowledge_engine.retriever import Retriever
    res = Retriever.get_instance().search(req.query, top_k=req.top_k, source_type=req.source_type)
    return {"summary": res["summary"], "hits": res.get("hits", []), "widget": res.get("widget")}


@router.post("/ingest/papers", summary="摄入 arxiv 论文（慢任务）")
async def ingest_papers_route(req: IngestPapersRequest):
    from knowledge_engine.ingest.web_source import ingest_papers
    try:
        return ingest_papers(req.queries, max_docs=req.max_docs)
    except Exception as e:  # noqa: BLE001
        logger.exception("摄入论文失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/ingest/arxiv", summary="摄入 arXiv 官方API论文（慢任务，一次性全量补齐用）")
async def ingest_arxiv_route(req: IngestArxivRequest):
    from knowledge_engine.ingest.arxiv_source import ingest_arxiv_papers
    try:
        return ingest_arxiv_papers(
            categories=req.categories, keywords=req.keywords,
            start_date=req.start_date, end_date=req.end_date,
            max_results=req.max_results, full_text=req.full_text,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("arXiv 摄入失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/ingest/web", summary="通用 web/EDGAR 摄入（慢任务）")
async def ingest_web_route(req: IngestWebRequest):
    from knowledge_engine.ingest.web_source import WebSearchSource
    try:
        return WebSearchSource().fetch(
            req.queries, source_type=req.source_type, site=req.site,
            use_sec=req.use_sec, max_docs=req.max_docs,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("web 摄入失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==================== A股法定财报批量摄入（cninfo，常驻后台任务） ====================
# 全市场几千只、跑几小时，不适合塞进一次 HTTP 请求/响应里（跟 ingest/papers、
# ingest/web 那种同步慢任务不一样）——起停+轮询状态，跟 ScrapeMonitorPanel
# 用的 sessions 轮询是同一套模式，数据监控页里常驻展示。

@router.post("/ingest/cninfo/start", summary="启动全市场 A股法定财报批量摄入（后台任务）")
async def ingest_cninfo_start(req: IngestCninfoStartRequest):
    from knowledge_engine.ingest.cninfo_job import cninfo_job
    return cninfo_job.start(
        categories=req.categories, per_category=req.per_category, max_pages=req.max_pages,
        start_date=req.start_date, end_date=req.end_date,
    )


@router.get("/ingest/cninfo/status", summary="查询批量摄入进度")
async def ingest_cninfo_status():
    from knowledge_engine.ingest.cninfo_job import cninfo_job
    return cninfo_job.snapshot()


@router.post("/ingest/cninfo/stop", summary="停止批量摄入（处理完当前这只后停）")
async def ingest_cninfo_stop():
    from knowledge_engine.ingest.cninfo_job import cninfo_job
    return cninfo_job.stop()


# ==================== 东财研报批量摄入（常驻后台任务） ====================
# 同 cninfo：全市场几千只、全文PDF模式下要跑数小时，起停+轮询状态，同一套模式。

@router.post("/ingest/research_report/start", summary="启动全市场东财研报批量摄入（后台任务）")
async def ingest_research_report_start(req: IngestResearchReportStartRequest):
    from knowledge_engine.ingest.research_report_job import research_report_job
    return research_report_job.start(
        limit_per_symbol=req.limit_per_symbol, full_text=req.full_text,
        start_date=req.start_date, end_date=req.end_date,
    )


@router.get("/ingest/research_report/status", summary="查询研报批量摄入进度")
async def ingest_research_report_status():
    from knowledge_engine.ingest.research_report_job import research_report_job
    return research_report_job.snapshot()


@router.post("/ingest/research_report/stop", summary="停止研报批量摄入（处理完当前这只后停）")
async def ingest_research_report_stop():
    from knowledge_engine.ingest.research_report_job import research_report_job
    return research_report_job.stop()


@router.post("/ingest/research_report/upgrade_full_text",
             summary="把已存在的仅元数据研报文档升级为全文（绕开doc_id固化，同步慢任务）")
async def upgrade_research_report_full_text(req: UpgradeResearchReportFullTextRequest):
    from knowledge_engine.ingest.research_report_source import upgrade_existing_to_full_text
    try:
        return upgrade_existing_to_full_text(req.symbols)
    except Exception as e:  # noqa: BLE001
        logger.exception("研报全文升级失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==================== Alpha Ideas ====================

def _idea_dict(idea) -> dict:
    return {
        "idea_id": idea.idea_id, "source_doc_id": idea.source_doc_id,
        "title": idea.title, "hypothesis": idea.hypothesis,
        "factor_definition": idea.factor_definition,
        "optimization_goal": idea.optimization_goal, "rationale": idea.rationale,
        "suggested_symbols": json.loads(idea.suggested_symbols or "[]"),
        "status": idea.status, "alpha_lab_session_id": idea.alpha_lab_session_id,
        "best_composite_score": idea.best_composite_score,
        "created_at": idea.created_at.isoformat() if idea.created_at else None,
    }


@router.get("/ideas", summary="alpha ideas 列表")
async def list_ideas(status: Optional[str] = None, limit: int = 100):
    from knowledge_engine.store import KnowledgeStore
    return [_idea_dict(i) for i in KnowledgeStore().list_ideas(status=status, limit=limit)]


@router.post("/ideas/mine", summary="从已入库论文提炼 alpha（慢任务，调 LLM）")
async def mine_ideas_route(req: MineRequest):
    from knowledge_engine.idea_miner import IdeaMiner
    try:
        return IdeaMiner().mine_ideas(limit=req.limit)
    except Exception as e:  # noqa: BLE001
        logger.exception("提炼 alpha 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/ideas/{idea_id}/backtest", summary="把 alpha idea 送 Alpha Lab 回测（很慢）")
async def backtest_idea_route(idea_id: str, req: BacktestRequest):
    from knowledge_engine.idea_miner import IdeaMiner
    try:
        return IdeaMiner().run_backtest(
            idea_id, data_start=req.data_start, data_end=req.data_end,
            symbols=req.symbols, max_iterations=req.max_iterations,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("回测 alpha idea 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==================== 抓取进度（sessionID 可追溯） ====================

@router.post("/scrape/stream", summary="流式抓取（带 sessionID 的进度）")
async def scrape_stream(req: ScrapeStreamRequest):
    """
    触发一次 scrape()，流式返回抓取过程（NDJSON）。

    第一行先给 session_created 带上 session_id；之后每个 stage 一行；
    最后一行是 done/error。session_id 也会自动登记进
    acquisition.browser.sessions 的内存注册表，可用 GET /scrape/sessions 查询，
    不局限于本次连接——MoneyBill 聊天里触发的 scrape 同样会出现在那份列表里。
    """
    from acquisition.crawler.reverse import scrape
    from acquisition.browser import sessions

    session_id = sessions.new_session_id()

    def sync_gen():
        q: "queue.Queue" = queue.Queue()
        sentinel = object()

        def on_progress(stage: str, fields: dict):
            q.put(json.dumps({"event": "progress", "session_id": session_id,
                              "stage": stage, **fields}, ensure_ascii=False, default=str) + "\n")

        def run():
            try:
                result = scrape(req.url, want=req.want, endpoint=req.endpoint,
                                 params=req.params, session_id=session_id, on_progress=on_progress)
                q.put(json.dumps({"event": "done", "session_id": session_id,
                                  "result": result}, ensure_ascii=False, default=str) + "\n")
            except Exception as e:  # noqa: BLE001 — 异常也要报给前端，不能让流悬空
                q.put(json.dumps({"event": "error", "session_id": session_id,
                                  "message": str(e)[:300]}, ensure_ascii=False) + "\n")
            finally:
                q.put(sentinel)

        threading.Thread(target=run, daemon=True).start()

        yield json.dumps({"event": "session_created", "session_id": session_id},
                          ensure_ascii=False) + "\n"
        while True:
            item = q.get()
            if item is sentinel:
                break
            yield item

    return StreamingResponse(
        bridge_sync_stream(sync_gen()),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/scrape/sessions", summary="抓取会话列表（含聊天里触发的）")
async def list_scrape_sessions(limit: int = 50):
    from acquisition.browser import sessions
    return sessions.list_sessions(limit=limit)
