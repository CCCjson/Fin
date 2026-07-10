"""SQLAlchemy engine / session 的建库样板 —— 全项目唯一实现。

主库（`data_engine/storage/database.py`，market.db）与知识库
（`knowledge_engine/database.py`，knowledge.db）此前各写了一份：`NullPool` +
`check_same_thread=False` + 同一段 WAL/busy_timeout 的 PRAGMA listener +
逐字符相同的 `sessionmaker(...)`。

**本模块只负责「构造」**。建表、字段迁移、数据回填（`init_db` /
`init_knowledge_db` / `backfill_stock_info`）留在各自模块——那是业务，不是样板。

`Base` / `KnowledgeBase`（各自 `declarative_base()`）与 `scoped_session(...)` 也
不抽：两库的 Base 必须隔离（`create_all` 只建本库的表），把差异硬塞进工厂是过度设计。
"""
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def _is_sqlite(db_url: str) -> bool:
    """沿用主库 database.py 的原判定口径，不改语义。"""
    return "sqlite" in db_url


def make_sqlite_engine(db_url: str, *, echo: bool = False) -> Engine:
    """按项目统一口径创建 Engine。

    sqlite：
      - `NullPool` —— 每个 session 独立连接，彻底隔离并发写入冲突
      - `check_same_thread=False` —— 允许跨线程用同一连接
      - connect 事件挂 `PRAGMA journal_mode=WAL` + `busy_timeout=30000`
        （多线程并发读写 + 等锁 30 秒，别改小）

    非 sqlite（如 postgres，仓内暂无实证）：`connect_args={}` + SQLAlchemy 默认池，
    不挂 PRAGMA listener。保守保留主库原有的条件分支语义。
    """
    is_sqlite = _is_sqlite(db_url)
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
        poolclass=NullPool if is_sqlite else None,
        echo=echo,
    )

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """`sessionmaker(autocommit=False, autoflush=False, bind=engine)`。两库逐字符相同。"""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)
