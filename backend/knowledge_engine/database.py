"""
知识库独立数据库连接 — 与主库 market.db 完全隔离。

为什么独立库（见方案核心决策①）：知识全文 + 向量膨胀大（万篇论文 ≈ 2GB+），
放独立 knowledge.db 避免拖累 6GB 主库的 WAL / 备份。

注意：这里只管 SQLAlchemy 业务表（documents/chunks/ideas）。vec0 虚拟表由
vector_store.py 用独立 sqlite3 短连接管理（ORM 的 create_all 不认虚拟表，
且建表前要先 load_extension）。两者共享同一个 knowledge.db 文件，WAL 下并发安全。
"""
from pathlib import Path

from sqlalchemy.orm import scoped_session, declarative_base

from common.db import make_session_factory, make_sqlite_engine
from knowledge_engine.config import get_db_url, get_db_path

# 知识库专用基类（与主库 Base 隔离，create_all 只建本库的表）
KnowledgeBase = declarative_base()

# 确保数据目录存在
Path(get_db_path()).parent.mkdir(parents=True, exist_ok=True)

_DB_URL = get_db_url()

# 引擎构造（NullPool + WAL + busy_timeout）统一在 common/db.py，与主库同一份
engine = make_sqlite_engine(_DB_URL)
SessionLocal = make_session_factory(engine)
ScopedSession = scoped_session(SessionLocal)


def init_knowledge_db() -> None:
    """初始化知识库：建业务表 + vec0 虚拟表。幂等，可重复调用。"""
    from knowledge_engine import models  # noqa: F401 触发表定义注册
    KnowledgeBase.metadata.create_all(bind=engine)

    # vec0 虚拟表走独立连接建（ORM 管不了虚拟表）
    from knowledge_engine.vector_store import ensure_vec_table
    ensure_vec_table()


def get_session():
    """获取知识库会话（直接使用）。"""
    return SessionLocal()
