"""
知识库业务表仓库（SQLAlchemy）— 增删查 Document/Chunk/AlphaIdea。

与 data_engine/storage/repository.py 风格一致，但自管 session（调用方是 pipeline/
retriever/idea_miner，不走 FastAPI 依赖注入）。每个方法用一个短 session。
"""
import hashlib
import json
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import select

from knowledge_engine.database import get_session
from knowledge_engine.models import KnowledgeDocument, KnowledgeChunk, AlphaIdea


def make_doc_id(source_type: str, native_id_or_url: str) -> str:
    """文档去重 id = md5(source_type:native_id/url)，复刻 NewsArticle.article_id 思路。"""
    return hashlib.md5(f"{source_type}:{native_id_or_url}".encode("utf-8")).hexdigest()


class KnowledgeStore:
    """知识库仓库（无状态，方法内开短 session）。"""

    # ---------- 文档 ----------

    def doc_exists(self, doc_id: str) -> bool:
        with get_session() as s:
            return s.query(KnowledgeDocument.id).filter(
                KnowledgeDocument.doc_id == doc_id
            ).first() is not None

    def upsert_document(
        self,
        *,
        source_type: str,
        title: str,
        url: str = "",
        native_id: Optional[str] = None,
        authors: str = "",
        published_at: Optional[datetime] = None,
        language: str = "en",
        full_text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, bool]:
        """插入文档（doc_id 命中则跳过）。返回 (doc_id, is_new)。"""
        doc_id = make_doc_id(source_type, native_id or url or title)
        with get_session() as s:
            existing = s.query(KnowledgeDocument).filter(
                KnowledgeDocument.doc_id == doc_id
            ).first()
            if existing:
                return doc_id, False
            doc = KnowledgeDocument(
                doc_id=doc_id,
                source_type=source_type,
                title=title[:500],
                authors=(authors or "")[:500],
                url=(url or "")[:1000],
                published_at=published_at,
                language=language,
                full_text=full_text or None,
                doc_metadata=json.dumps(metadata, ensure_ascii=False) if metadata else None,
                status="pending",
            )
            s.add(doc)
            s.commit()
            return doc_id, True

    def update_document_content(self, doc_id: str, *, full_text: Optional[str] = None,
                                metadata: Optional[Dict[str, Any]] = None) -> None:
        """就地更新已存在文档的正文/元数据（配合 delete_chunks 做"升级重摄入"，
        绕开 upsert_document 对已存在 doc_id 的短路跳过）。"""
        with get_session() as s:
            doc = s.query(KnowledgeDocument).filter(KnowledgeDocument.doc_id == doc_id).first()
            if not doc:
                return
            if full_text is not None:
                doc.full_text = full_text
            if metadata is not None:
                doc.doc_metadata = json.dumps(metadata, ensure_ascii=False)
            doc.status = "pending"
            doc.chunk_count = 0
            s.commit()

    def set_document_status(self, doc_id: str, status: str, chunk_count: Optional[int] = None) -> None:
        with get_session() as s:
            doc = s.query(KnowledgeDocument).filter(KnowledgeDocument.doc_id == doc_id).first()
            if not doc:
                return
            doc.status = status
            if chunk_count is not None:
                doc.chunk_count = chunk_count
            if status == "embedded":
                doc.embedded_at = datetime.now()
            s.commit()

    def get_documents(self, doc_ids: List[str]) -> Dict[str, KnowledgeDocument]:
        """按 doc_id 批量取文档（detach 后可读属性），返回 {doc_id: doc}。"""
        if not doc_ids:
            return {}
        with get_session() as s:
            rows = s.query(KnowledgeDocument).filter(
                KnowledgeDocument.doc_id.in_(doc_ids)
            ).all()
            for r in rows:
                s.expunge(r)
            return {r.doc_id: r for r in rows}

    def list_documents(self, source_type: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[KnowledgeDocument]:
        with get_session() as s:
            q = s.query(KnowledgeDocument)
            if source_type and source_type != "all":
                q = q.filter(KnowledgeDocument.source_type == source_type)
            rows = q.order_by(KnowledgeDocument.fetched_at.desc()).limit(limit).offset(offset).all()
            for r in rows:
                s.expunge(r)
            return rows

    # ---------- 切片 ----------

    def add_chunks(self, doc_id: str, chunks: List[Dict[str, Any]]) -> List[int]:
        """批量写切片，返回自增 id 列表（顺序与入参一致，供向量写入用 rowid）。"""
        if not chunks:
            return []
        with get_session() as s:
            rows = [
                KnowledgeChunk(
                    doc_id=doc_id,
                    seq=ch["seq"],
                    text=ch["text"],
                    token_count=ch.get("token_count"),
                    section=ch.get("section"),
                    embedded=0,
                )
                for ch in chunks
            ]
            s.add_all(rows)
            s.flush()            # 一次 flush 批量 INSERT，flush 后各 row.id 已回填（同类无依赖，
                                 # unit-of-work 按 add 顺序插入，ids 顺序 = 入参顺序）
            ids = [row.id for row in rows]
            s.commit()
        return ids

    def delete_chunks(self, doc_id: str) -> List[int]:
        """删除某文档的全部切片，返回被删的 chunk id 列表（调用方需同步删向量表）。"""
        with get_session() as s:
            rows = s.query(KnowledgeChunk.id).filter(KnowledgeChunk.doc_id == doc_id).all()
            ids = [r[0] for r in rows]
            if ids:
                s.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == doc_id).delete(synchronize_session=False)
                s.commit()
            return ids

    def mark_chunks_embedded(self, chunk_ids: List[int]) -> None:
        if not chunk_ids:
            return
        with get_session() as s:
            s.query(KnowledgeChunk).filter(
                KnowledgeChunk.id.in_(chunk_ids)
            ).update({KnowledgeChunk.embedded: 1}, synchronize_session=False)
            s.commit()

    def get_unembedded_chunks(self, limit: int = 256) -> List[KnowledgeChunk]:
        """取尚未进向量表的切片（断点续传 / 重扫补写）。"""
        with get_session() as s:
            rows = s.query(KnowledgeChunk).filter(
                KnowledgeChunk.embedded == 0
            ).limit(limit).all()
            for r in rows:
                s.expunge(r)
            return rows

    def get_chunks_by_ids(self, chunk_ids: List[int]) -> Dict[int, KnowledgeChunk]:
        if not chunk_ids:
            return {}
        with get_session() as s:
            rows = s.query(KnowledgeChunk).filter(KnowledgeChunk.id.in_(chunk_ids)).all()
            for r in rows:
                s.expunge(r)
            return {r.id: r for r in rows}

    # ---------- alpha ideas ----------

    def add_idea(self, *, source_doc_id: str, title: str, hypothesis: str = "",
                 factor_definition: str = "", optimization_goal: str = "",
                 rationale: str = "", suggested_symbols: Optional[List[str]] = None) -> str:
        idea_id = hashlib.md5(f"{source_doc_id}:{title}".encode("utf-8")).hexdigest()
        with get_session() as s:
            if s.query(AlphaIdea.id).filter(AlphaIdea.idea_id == idea_id).first():
                return idea_id
            s.add(AlphaIdea(
                idea_id=idea_id,
                source_doc_id=source_doc_id,
                title=title[:300],
                hypothesis=hypothesis,
                factor_definition=factor_definition,
                optimization_goal=optimization_goal,
                rationale=rationale,
                suggested_symbols=json.dumps(suggested_symbols or [], ensure_ascii=False),
                status="proposed",
            ))
            s.commit()
        return idea_id

    def list_ideas(self, status: Optional[str] = None, limit: int = 100) -> List[AlphaIdea]:
        with get_session() as s:
            q = s.query(AlphaIdea)
            if status:
                q = q.filter(AlphaIdea.status == status)
            rows = q.order_by(AlphaIdea.created_at.desc()).limit(limit).all()
            for r in rows:
                s.expunge(r)
            return rows

    def get_idea(self, idea_id: str) -> Optional[AlphaIdea]:
        with get_session() as s:
            r = s.query(AlphaIdea).filter(AlphaIdea.idea_id == idea_id).first()
            if r:
                s.expunge(r)
            return r

    def update_idea(self, idea_id: str, **fields) -> None:
        with get_session() as s:
            r = s.query(AlphaIdea).filter(AlphaIdea.idea_id == idea_id).first()
            if not r:
                return
            for k, v in fields.items():
                setattr(r, k, v)
            s.commit()

    def doc_ids_without_ideas(self, source_type: str = "paper", limit: int = 50) -> List[str]:
        """取尚未提炼过 alpha 的论文 doc_id（idea_miner 扫描用）。"""
        with get_session() as s:
            mined = select(AlphaIdea.source_doc_id)
            rows = s.query(KnowledgeDocument.doc_id).filter(
                KnowledgeDocument.source_type == source_type,
                KnowledgeDocument.status == "embedded",
                ~KnowledgeDocument.doc_id.in_(mined),
            ).limit(limit).all()
            return [r[0] for r in rows]

    # ---------- 统计 ----------

    def stats(self) -> Dict[str, Any]:
        with get_session() as s:
            from sqlalchemy import func as f
            by_source = dict(
                s.query(KnowledgeDocument.source_type, f.count(KnowledgeDocument.id))
                .group_by(KnowledgeDocument.source_type).all()
            )
            doc_total = s.query(f.count(KnowledgeDocument.id)).scalar() or 0
            chunk_total = s.query(f.count(KnowledgeChunk.id)).scalar() or 0
            idea_total = s.query(f.count(AlphaIdea.id)).scalar() or 0
        return {
            "documents": doc_total,
            "documents_by_source": by_source,
            "chunks": chunk_total,
            "ideas": idea_total,
        }
