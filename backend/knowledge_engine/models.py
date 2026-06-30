"""
知识库 ORM 表定义 — 绑定独立 knowledge.db 的 KnowledgeBase。

风格对齐 data_engine/storage/models.py。三张业务表：
- KnowledgeDocument：一篇论文/研报/财报/内部数据
- KnowledgeChunk：文档切片（id 同时作为 vec0 虚拟表的 rowid）
- AlphaIdea：idea_miner 从论文提炼出的结构化 alpha 假设

向量表 vec_chunks 是 vec0 虚拟表，不在此定义（见 vector_store.py）。
"""
from sqlalchemy import Column, String, Float, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.sql import func

from knowledge_engine.database import KnowledgeBase


class KnowledgeDocument(KnowledgeBase):
    """知识文档表 — 一篇论文/研报/财报/内部数据为一条。"""
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # md5(source_type + native_id/url) 去重（复刻 NewsArticle.article_id 思路）
    doc_id = Column(String(64), unique=True, nullable=False, index=True)
    # paper / research / financial / internal / news
    source_type = Column(String(20), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    authors = Column(String(500))            # 论文作者 / 机构
    url = Column(String(1000))               # 原文链接
    published_at = Column(DateTime, index=True)
    language = Column(String(5), default="en")
    full_text = Column(Text)                 # 原文全文（可选不落库，膨胀大户）
    doc_metadata = Column(Text)              # JSON: arxiv_id/categories/symbol/report_period...
    chunk_count = Column(Integer, default=0)
    # pending -> chunked -> embedded / failed
    status = Column(String(20), default="pending", index=True)
    fetched_at = Column(DateTime, server_default=func.now())
    embedded_at = Column(DateTime)

    __table_args__ = (
        Index("idx_kdoc_source_published", "source_type", "published_at"),
    )

    def __repr__(self):
        return f"<KnowledgeDocument(doc_id={self.doc_id}, source={self.source_type}, title={self.title[:30]!r})>"


class KnowledgeChunk(KnowledgeBase):
    """切片表 — 文档切成的检索粒度。id 直接作为 vec0 的 rowid。"""
    __tablename__ = "knowledge_chunks"

    # ★ 自增 id 同时作为 vec_chunks 虚拟表的 rowid，KNN 返回 rowid 一对一回 join
    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(64), ForeignKey("knowledge_documents.doc_id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)    # 文档内第几片
    text = Column(Text, nullable=False)
    token_count = Column(Integer)
    section = Column(String(200))            # 可选：所属章节（Abstract/Methodology…）
    embedded = Column(Integer, default=0, index=True)  # 0/1 是否已进向量表（增量/重扫用）
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_kchunk_doc_seq", "doc_id", "seq"),
    )

    def __repr__(self):
        return f"<KnowledgeChunk(id={self.id}, doc_id={self.doc_id}, seq={self.seq})>"


class AlphaIdea(KnowledgeBase):
    """idea_miner 提炼出的结构化 alpha 假设。"""
    __tablename__ = "alpha_ideas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idea_id = Column(String(64), unique=True, nullable=False, index=True)
    source_doc_id = Column(String(64), ForeignKey("knowledge_documents.doc_id"), index=True)
    title = Column(String(300), nullable=False)
    hypothesis = Column(Text)                # 一句话因子逻辑
    factor_definition = Column(Text)         # 因子/信号的可计算定义（自然语言 + 伪公式）
    optimization_goal = Column(Text)         # ★ 直接喂 AlphaLabEngine.start_session 的自然语言目标
    rationale = Column(Text)                 # 为什么可能有 alpha
    suggested_symbols = Column(Text)         # JSON: 适用标的 / 市场
    # proposed / queued / backtesting / validated / rejected
    status = Column(String(20), default="proposed", index=True)
    alpha_lab_session_id = Column(String(50))   # 回测后回填
    best_composite_score = Column(Float)        # 回测结果回填
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<AlphaIdea(idea_id={self.idea_id}, status={self.status}, title={self.title[:30]!r})>"
