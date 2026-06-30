"""
WebSearchSource — 用 websearch 原语把外部论文/研报正文摄入知识库。

流程：query 列表 → web_search/sec_search 取候选 url → read_url 取正文 →
复用 IngestPipeline.ingest_text(native_id=url) 入库（切片+嵌入+落库）。

三层去重：进程内 seen_urls + 库级 native_id=url 幂等 + min_chars 过滤抓取失败。
**批量搜索是慢任务（DDG 8s/次 + 下载）→ 放后台脚本/任务调用，绝不进 web 同步请求路径。**
"""
from typing import List, Optional, Dict, Any

from loguru import logger

from knowledge_engine.ingest import IngestPipeline
from knowledge_engine.websearch import web_search, sec_search, read_url
from knowledge_engine.config import get_search_queries


class WebSearchSource:
    def __init__(self) -> None:
        self.pipeline = IngestPipeline()

    def fetch(
        self,
        queries: Optional[List[str]] = None,
        *,
        source_type: str = "research",
        site: Optional[str] = None,
        per_query: int = 8,
        min_chars: int = 500,
        use_sec: bool = False,
        max_docs: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        按 query 列表摄入。
        - queries 为空时用 config.get_search_queries()（KNOWLEDGE_SEARCH_QUERIES）。
        - site：限定搜索站点（如 'arxiv.org'）。
        - use_sec=True：走 SEC EDGAR 而非 DDG。
        返回 {ingested, skipped, failed, docs:[{doc_id,title,url,status}]}。
        """
        queries = queries or get_search_queries()
        if not queries:
            return {"ingested": 0, "skipped": 0, "failed": 0, "docs": [],
                    "note": "无 query（传 queries 或设 KNOWLEDGE_SEARCH_QUERIES）"}

        seen_urls: set[str] = set()
        ingested = skipped = failed = 0
        docs: List[Dict[str, Any]] = []

        for q in queries:
            hits = sec_search(q, max_results=per_query) if use_sec \
                else web_search(q, max_results=per_query, site=site)
            logger.info(f"WebSearchSource 搜「{q}」命中 {len(hits)} 条")
            for h in hits:
                url = h.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                doc = read_url(url)
                text = doc.get("text", "")
                if len(text) < min_chars:
                    failed += 1
                    logger.debug(f"正文太短/抓取失败，跳过: {url}")
                    continue

                # 优先用 DDG 搜索结果标题（干净），PDF 元数据标题常是图名/空，不可靠
                title = h.get("title") or doc.get("title") or url
                res = self.pipeline.ingest_text(
                    source_type=source_type,
                    title=title,
                    text=text,
                    url=url,
                    native_id=url,                 # url 作幂等键，库级去重
                    language="en",
                    metadata={"query": q, "search_source": h.get("source")},
                )
                if res["is_new"]:
                    ingested += 1
                else:
                    skipped += 1
                docs.append({"doc_id": res["doc_id"], "title": doc.get("title") or h.get("title"),
                             "url": url, "status": res["status"]})

                if max_docs and ingested >= max_docs:
                    logger.info(f"达到 max_docs={max_docs}，停止摄入")
                    return {"ingested": ingested, "skipped": skipped, "failed": failed, "docs": docs}

        logger.info(f"WebSearchSource 摄入完成：新增 {ingested}，跳过 {skipped}，失败 {failed}")
        return {"ingested": ingested, "skipped": skipped, "failed": failed, "docs": docs}


def ingest_papers(queries: List[str], per_query: int = 6, max_docs: int = 10) -> Dict[str, Any]:
    """便捷入口：从 arxiv 摄入论文（source_type='paper'）。"""
    return WebSearchSource().fetch(
        queries, source_type="paper", site="arxiv.org",
        per_query=per_query, max_docs=max_docs,
    )
