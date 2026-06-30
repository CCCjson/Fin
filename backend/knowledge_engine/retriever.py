"""
RAG 检索器 — embed query → vec0 KNN → join chunk/document → 过滤 → 拼引用。

source_type 过滤（MVP 方案）：先 KNN 取 top_k*OVERSAMPLE，回 join 文档后在 Python 侧
按 source_type 过滤、截断 top_k。量级（几十万 chunk 内）够快。
"""
from typing import Dict, List, Optional, Any

from knowledge_engine.embedding import Embedder
from knowledge_engine.store import KnowledgeStore
from knowledge_engine import vector_store

_OVERSAMPLE = 4
_SNIPPET_CHARS = 400


class Retriever:
    _instance: Optional["Retriever"] = None

    def __init__(self) -> None:
        self.store = KnowledgeStore()

    @classmethod
    def get_instance(cls) -> "Retriever":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search(self, query: str, top_k: int = 5, source_type: str = "all") -> Dict[str, Any]:
        """检索并返回 {summary, widget, hits}（符合 agent 工具协议）。"""
        if not query or not query.strip():
            return {"summary": "检索 query 为空。", "hits": []}

        qvec = Embedder.get_instance().encode_query(query)
        raw = vector_store.knn(qvec, top_k * _OVERSAMPLE)
        if not raw:
            return {"summary": "知识库暂无相关内容（可能尚未摄入数据）。", "hits": []}

        chunk_ids = [cid for cid, _ in raw]
        chunks = self.store.get_chunks_by_ids(chunk_ids)
        doc_ids = list({c.doc_id for c in chunks.values()})
        docs = self.store.get_documents(doc_ids)

        hits: List[Dict[str, Any]] = []
        for cid, dist in raw:
            ch = chunks.get(cid)
            if not ch:
                continue
            doc = docs.get(ch.doc_id)
            if not doc:
                continue
            if source_type and source_type != "all" and doc.source_type != source_type:
                continue
            hits.append({
                "chunk_id": cid,
                "doc_id": ch.doc_id,
                "distance": round(dist, 4),
                "title": doc.title,
                "source_type": doc.source_type,
                "url": doc.url or "",
                "published_at": doc.published_at.strftime("%Y-%m-%d") if doc.published_at else "",
                "text": ch.text,
            })
            if len(hits) >= top_k:
                break

        if not hits:
            return {"summary": f"未检索到 source_type={source_type} 的相关内容。", "hits": []}

        return {
            "summary": self._format_summary(query, hits),
            "widget": self._format_widget(hits),
            "hits": hits,
        }

    @staticmethod
    def _format_summary(query: str, hits: List[Dict]) -> str:
        lines = [f"知识库检索「{query}」命中 {len(hits)} 条："]
        for i, h in enumerate(hits, 1):
            snippet = h["text"][:_SNIPPET_CHARS].replace("\n", " ")
            meta = f"{h['source_type']}{('，' + h['published_at']) if h['published_at'] else ''}"
            lines.append(f"[{i}]《{h['title']}》({meta})\n{snippet}…")
        lines.append("\n（回答时请用 [编号] 标注引用出处。）")
        return "\n".join(lines)

    @staticmethod
    def _format_widget(hits: List[Dict]) -> Dict[str, Any]:
        return {
            "type": "knowledge_sources",
            "title": "知识库引用",
            "data": {
                "sources": [
                    {"index": i, "title": h["title"], "source_type": h["source_type"],
                     "url": h["url"], "published_at": h["published_at"],
                     "distance": h["distance"]}
                    for i, h in enumerate(hits, 1)
                ]
            },
        }
