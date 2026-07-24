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
import os

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def _is_sqlite(db_url: str) -> bool:
    """沿用主库 database.py 的原判定口径，不改语义。"""
    return "sqlite" in db_url


def _fullfsync_enabled() -> bool:
    """是否启用 F_FULLFSYNC（默认开）。`FIN_SQLITE_FULLFSYNC=false` 关掉。

    **为什么默认开**：macOS 上普通 `fsync()` 只把数据推到**硬盘自身的缓存**就返回，
    并不保证落到闪存。库在外置 USB 盘（T9）上，盘一断电/被拔，那块缓存里已经
    「提交成功」的事务就没了。只有 `F_FULLFSYNC` 才强制真正落盘，而 SQLite 用不用
    它由 `PRAGMA fullfsync` 决定。

    **代价**：每次 COMMIT 都要等真实落盘，写入明显变慢。如果哪天发现写入吞吐扛不住
    （尤其是全市场回补这类批量写），用 `FIN_SQLITE_FULLFSYNC=false` 一键退回，
    不用改代码。
    """
    return os.getenv("FIN_SQLITE_FULLFSYNC", "true").strip().lower() not in (
        "false", "0", "no", "off")


def make_sqlite_engine(db_url: str, *, echo: bool = False) -> Engine:
    """按项目统一口径创建 Engine。

    sqlite：
      - `NullPool` —— 每个 session 独立连接，彻底隔离并发写入冲突
      - `check_same_thread=False` —— 允许跨线程用同一连接
      - connect 事件挂 `PRAGMA journal_mode=WAL` + `busy_timeout=30000`
        （多线程并发读写 + 等锁 30 秒，别改小）
      - `PRAGMA fullfsync` + `checkpoint_fullfsync`（默认开，见 `_fullfsync_enabled`）
        —— 库在外置盘上，普通 fsync 挡不住拔盘/断电丢已提交事务

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
            if _fullfsync_enabled():
                # 必须赶在 journal_mode=WAL 之前：切 WAL 本身就会写盘同步，
                # 让它也走 F_FULLFSYNC。
                # fullfsync 管 WAL 的同步，checkpoint_fullfsync 管 checkpoint
                # 回写主库那一次——两个都要，只开一个仍有缺口。
                cur.execute("PRAGMA fullfsync=1")
                cur.execute("PRAGMA checkpoint_fullfsync=1")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """`sessionmaker(autocommit=False, autoflush=False, bind=engine)`。两库逐字符相同。"""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)
