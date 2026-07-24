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


def test_fullfsync_is_on_by_default():
    """macOS 普通 fsync 只推到硬盘缓存；库在外置盘上，拔盘/断电会丢已提交事务。

    两个 PRAGMA 都得开：`fullfsync` 管 WAL 的同步，`checkpoint_fullfsync` 管
    checkpoint 回写主库那一次，只开一个仍留缺口。
    """
    engine = make_sqlite_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA fullfsync")).scalar() == 1
        assert conn.execute(text("PRAGMA checkpoint_fullfsync")).scalar() == 1


def test_fullfsync_can_be_turned_off_by_env(monkeypatch):
    """写入吞吐扛不住时要能一键退回，不用改代码（NullPool 下新连接即刻生效）。"""
    monkeypatch.setenv("FIN_SQLITE_FULLFSYNC", "false")
    engine = make_sqlite_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA fullfsync")).scalar() == 0


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
            assert conn.execute(text("PRAGMA fullfsync")).scalar() == 1
            assert conn.execute(text("PRAGMA checkpoint_fullfsync")).scalar() == 1


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


# ── 撞锁重试（2026-07-24 补：港美股回补每撞一次静默丢 ~189 行）──────────────

def _locked_error():
    from sqlalchemy.exc import OperationalError
    return OperationalError("INSERT ...", {}, Exception("database is locked"))


class _FakeSession:
    """按脚本决定第几次 commit 才成功。"""

    def __init__(self, fail_times: int, exc_factory=_locked_error):
        self.fail_times = fail_times
        self.exc_factory = exc_factory
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1
        if self.commits <= self.fail_times:
            raise self.exc_factory()

    def rollback(self):
        self.rollbacks += 1


def test_commit_with_retry_survives_transient_lock(monkeypatch):
    """撞锁是瞬时争用不是坏数据——重试就该成功，绝不能让调用方当成坏数据丢弃。"""
    import common.db as db_mod
    monkeypatch.setattr(db_mod.time, "sleep", lambda _s: None)   # 别真睡

    s = _FakeSession(fail_times=2)
    db_mod.commit_with_retry(s, tries=6, base_delay=0.01)
    assert s.commits == 3        # 失败 2 次 + 成功 1 次
    assert s.rollbacks == 2      # 每次失败都要 rollback 放掉读锁


def test_commit_with_retry_reraises_non_lock_errors(monkeypatch):
    """真·坏数据（NaN、越界）不该被重试掩盖，要原样抛给调用方处理。"""
    from sqlalchemy.exc import OperationalError

    import common.db as db_mod
    monkeypatch.setattr(db_mod.time, "sleep", lambda _s: None)

    def _bad_data():
        return OperationalError("INSERT ...", {}, Exception("NOT NULL constraint failed"))

    s = _FakeSession(fail_times=99, exc_factory=_bad_data)
    with pytest.raises(OperationalError):
        db_mod.commit_with_retry(s, tries=6, base_delay=0.01)
    assert s.commits == 1, "非撞锁错误必须立刻抛，不许重试"


def test_commit_with_retry_gives_up_after_budget(monkeypatch):
    """一直撞锁也得有个头，别无限挂着。"""
    from sqlalchemy.exc import OperationalError

    import common.db as db_mod
    monkeypatch.setattr(db_mod.time, "sleep", lambda _s: None)

    s = _FakeSession(fail_times=99)
    with pytest.raises(OperationalError):
        db_mod.commit_with_retry(s, tries=4, base_delay=0.01)
    assert s.commits == 4


def test_bulk_upsert_quotes_commits_with_retry():
    """真正的修复点：bulk_upsert_quotes 不许再用裸 session.commit()。

    它是港美股增量 / 深历史回补共用的写入口，调用方一律把异常当「这批数据有问题」
    整批丢弃——用裸 commit 就等于撞一次锁丢一批行。
    """
    import inspect

    from data_engine.deep_history import bulk_upsert as mod

    src = inspect.getsource(mod.bulk_upsert_quotes)
    # 只看可执行行：注释里为了讲清楚「别用裸 session.commit()」会出现该字样
    code = "\n".join(
        ln.split("#", 1)[0] for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "commit_with_retry" in code
    assert "session.commit()" not in code, "裸 commit 会让撞锁被当成坏数据丢弃"
