"""
外置金融大脑 knowledge_engine API —— 文档/检索/摄入/alpha ideas。

注意：摄入/提炼/回测是慢任务（DDG 限速 + LLM + 回测），同步返回，前端要给足超时
或放后台触发。批量摄入别频繁打。
"""
import json
from typing import Optional, List

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

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
    max_iterations: int = Field(8)


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
