"""13.4-1 行为基线：建库样板工厂。

主库与知识库此前各写一份 NullPool + WAL/busy_timeout 的建库代码。抽成
`common/db.py` 后，这几条必须一模一样地活着——它们不是装饰，是并发写入的根据：

- `NullPool`：每个 session 独立连接，隔离并发写冲突
- `journal_mode=WAL`：多线程并发读写
- `busy_timeout=30000`：等锁 30 秒（改小会在日线全量更新时炸出 database is locked）
"""
import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from common.db import _is_sqlite, make_session_factory, make_sqlite_engine

pytestmark = pytest.mark.baseline


@pytest.mark.parametrize("url,expected", [
    ("sqlite:///:memory:", True),
    ("sqlite:////abs/path/market.db", True),
    ("postgresql://u:p@h/db", False),
    ("mysql+pymysql://u:p@h/db", False),
])
def test_is_sqlite_keeps_the_original_substring_check(url, expected):
    """沿用主库 database.py 的原判定口径（`"sqlite" in url`），别改成 startswith。"""
    assert _is_sqlite(url) is expected


def test_sqlite_engine_uses_nullpool():
    engine = make_sqlite_engine("sqlite:///:memory:")
    assert isinstance(engine.pool, NullPool)


def test_sqlite_engine_sets_wal_and_busy_timeout():
    engine = make_sqlite_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        # :memory: 库拿不到 wal（内存库只能 memory），但 busy_timeout 一定要生效
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000


def test_session_factory_never_autocommits_or_autoflushes():
    """autoflush=True 会在读查询时偷偷 flush 半成品对象，交易系统里这是灾难。"""
    engine = make_sqlite_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    s = factory()
    try:
        assert s.autoflush is False
        assert s.get_bind() is engine
    finally:
        s.close()


# ── 真实两库的端到端断言（跑在生产 engine 上，只读 PRAGMA）────────────────

def test_both_real_engines_share_the_same_settings():
    from data_engine.storage.database import engine as market_engine
    from knowledge_engine.database import engine as knowledge_engine

    for engine in (market_engine, knowledge_engine):
        assert isinstance(engine.pool, NullPool)
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000


def test_migration_logic_stayed_in_its_own_module():
    """common/db.py 只管构造。建表/迁移/回填留在业务模块，别被搬走。"""
    import common.db as db_mod
    from data_engine.storage import database as market_mod
    from knowledge_engine import database as knowledge_mod

    assert not hasattr(db_mod, "init_db")
    assert not hasattr(db_mod, "backfill_stock_info")
    assert callable(market_mod.init_db)
    assert callable(market_mod.backfill_stock_info)
    assert callable(knowledge_mod.init_knowledge_db)
