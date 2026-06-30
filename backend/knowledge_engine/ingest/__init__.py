"""
摄入编排 IngestPipeline：（搜→读正文→）解析→切片→embed→落库。

P0 只用 internal_source（已有结构化数据转知识，无抓取）。P1 加 WebSearchSource。
全流程幂等：doc_id 去重 + chunk.embedded 标记，重跑只补未完成的。
"""
from typing import Dict, List, Optional, Any
from datetime import datetime

from loguru import logger

from knowledge_engine.store import KnowledgeStore
from knowledge_engine.chunker import chunk_text
from knowledge_engine.embedding import Embedder
from knowledge_engine import vector_store


class IngestPipeline:
    def __init__(self) -> None:
        self.store = KnowledgeStore()

    def ingest_text(
        self,
        *,
        source_type: str,
        title: str,
        text: str,
        url: str = "",
        native_id: Optional[str] = None,
        authors: str = "",
        published_at: Optional[datetime] = None,
        language: str = "en",
        metadata: Optional[Dict[str, Any]] = None,
        store_full_text: bool = True,
        pre_chunked: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        摄入一篇文档全流程。返回 {doc_id, is_new, chunks, status}。
        pre_chunked: 若已切好（如内部数据一条转一片），直接用，跳过 chunk_text。
        """
        doc_id, is_new = self.store.upsert_document(
            source_type=source_type, title=title, url=url, native_id=native_id,
            authors=authors, published_at=published_at, language=language,
            full_text=text if store_full_text else "", metadata=metadata,
        )
        if not is_new:
            return {"doc_id": doc_id, "is_new": False, "chunks": 0, "status": "exists"}

        try:
            chunks = pre_chunked if pre_chunked is not None else chunk_text(text)
            if not chunks:
                self.store.set_document_status(doc_id, "failed", chunk_count=0)
                return {"doc_id": doc_id, "is_new": True, "chunks": 0, "status": "failed"}

            chunk_ids = self.store.add_chunks(doc_id, chunks)
            self.store.set_document_status(doc_id, "chunked", chunk_count=len(chunks))

            # 批量编码 + 写 vec0
            vectors = Embedder.get_instance().encode_passages([c["text"] for c in chunks])
            vector_store.upsert(list(zip(chunk_ids, vectors)))
            self.store.mark_chunks_embedded(chunk_ids)
            self.store.set_document_status(doc_id, "embedded", chunk_count=len(chunks))

            return {"doc_id": doc_id, "is_new": True, "chunks": len(chunks), "status": "embedded"}
        except Exception as e:  # noqa: BLE001
            logger.exception(f"摄入失败 doc_id={doc_id}: {e}")
            self.store.set_document_status(doc_id, "failed")
            return {"doc_id": doc_id, "is_new": True, "chunks": 0, "status": "failed", "error": str(e)}

    def reembed_pending(self, limit: int = 256) -> int:
        """补嵌：把 embedded=0 的切片重新写入向量表（断点续传/补漏）。"""
        chunks = self.store.get_unembedded_chunks(limit=limit)
        if not chunks:
            return 0
        vectors = Embedder.get_instance().encode_passages([c.text for c in chunks])
        vector_store.upsert([(c.id, v) for c, v in zip(chunks, vectors)])
        self.store.mark_chunks_embedded([c.id for c in chunks])
        return len(chunks)
