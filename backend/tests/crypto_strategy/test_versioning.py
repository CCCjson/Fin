"""策略版本化 + 软退役（S2）。

三条不变式，每条都对应一个「不做就会丢东西」的场景：

1. **同 family 永远最多一个 armed** —— 不然 v1 和 v2 会同时对同一批币下单，
   而 `pending.has_open` 的去重是按 `strategy_id` 的，**拦不住跨版本重复**。
2. **软退役不删战绩** —— 旧实现 `retire()` 连 `crypto_strategy_runs` 一起 delete，
   退役即失忆。而 AI 提案样本一年才 12-24 条，删一条少一条。
3. **各版本的 runs 互不串** —— 「改完变好了没」全靠这个。
"""
import json
from datetime import datetime, timedelta

import pytest

from crypto_strategy.service import StrategyError
from crypto_strategy.service import crypto_strategy_service as svc
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    CryptoPendingOrder,
    CryptoStrategy,
    CryptoStrategyProposal,
    CryptoStrategyRun,
)

_SPEC = {
    "name": "测试策略",
    "universe": {"symbols": ["BTCUSDT.BN"]},
    "entry_rules": {"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
    "exit_rules": {"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}},
    "guardrails": {"per_order_notional_usdt": 100, "max_orders_per_day": 3},
}


def _spec(**over):
    import copy
    s = copy.deepcopy(_SPEC)
    s.update(over)
    return s


@pytest.fixture
def clean():
    yield
    s = get_session()
    try:
        for m in (CryptoStrategyRun, CryptoPendingOrder, CryptoStrategyProposal,
                  CryptoStrategy):
            s.query(m).delete()
        s.commit()
    finally:
        s.close()


def _mk(name="测试策略"):
    from crypto_intel_engine.dsl import CryptoStrategySpec
    return svc.compile_and_persist(CryptoStrategySpec(**_spec(name=name)),
                                   do_backtest=False)


def _fork(base_id, **over):
    from crypto_intel_engine.dsl import CryptoStrategySpec
    return svc.fork_version(base_id, CryptoStrategySpec(**_spec(**over)),
                            do_backtest=False)


def _mk_run(strategy_id, minutes_ago=10):
    s = get_session()
    try:
        s.add(CryptoStrategyRun(strategy_id=strategy_id, status="evaluated", mode="live",
                                started_at=datetime.utcnow() - timedelta(minutes=minutes_ago)))
        s.commit()
    finally:
        s.close()


# ── 1. family / version ───────────────────────────────────────────────────

def test_new_strategy_is_v1_of_its_own_family(clean):
    r = _mk()
    assert r["summary"]["version"] == 1
    assert r["summary"]["family_id"].startswith("SF-")
    assert r["summary"]["family_id"] != r["strategy_id"]


def test_fork_increments_version_within_same_family(clean):
    base = _mk()
    f1 = _fork(base["strategy_id"], name="改过的")
    f2 = _fork(f1["strategy_id"], interval_minutes=15)
    assert [f1["version"], f2["version"]] == [2, 3]
    assert f1["family_id"] == f2["family_id"] == base["summary"]["family_id"]


def test_fork_never_auto_arms(clean):
    """⛔「批准了提案」和「让它上线」是两个决定。"""
    base = _mk()
    svc.arm(base["strategy_id"])
    f = _fork(base["strategy_id"], name="新版")
    assert f["summary"]["enabled"] is False
    assert f["summary"]["status"] == "draft"
    # 旧版本原样继续跑
    assert svc.get_strategy(base["strategy_id"])["status"] == "armed"


def test_list_strategies_shows_only_latest_version_by_default(clean):
    base = _mk()
    f = _fork(base["strategy_id"], name="v2")
    ids = {s["strategy_id"] for s in svc.list_strategies()}
    assert ids == {f["strategy_id"]}
    all_ids = {s["strategy_id"] for s in svc.list_strategies(latest_only=False)}
    assert all_ids == {base["strategy_id"], f["strategy_id"]}


def test_list_family_is_ordered_by_version(clean):
    base = _mk()
    f = _fork(base["strategy_id"], name="v2")
    fam = svc.list_family(base["summary"]["family_id"])
    assert [x["version"] for x in fam] == [1, 2]
    assert fam[-1]["strategy_id"] == f["strategy_id"]


# ── 2. 同 family 最多一个 armed ───────────────────────────────────────────

def test_arming_v2_supersedes_v1(clean):
    base = _mk()
    svc.arm(base["strategy_id"])
    f = _fork(base["strategy_id"], name="v2")

    res = svc.arm(f["strategy_id"])
    assert res["superseded"] == [base["strategy_id"]]
    old = svc.get_strategy(base["strategy_id"])
    assert old["status"] == "superseded" and old["enabled"] is False
    # 🔴 最要紧的一条：同族在跑的版本只剩一个
    live = [s for s in svc.list_strategies(latest_only=False) if s["enabled"]]
    assert [s["strategy_id"] for s in live] == [f["strategy_id"]]


def test_arming_does_not_touch_other_families(clean):
    a = _mk("甲")
    b = _mk("乙")
    svc.arm(a["strategy_id"])
    svc.arm(b["strategy_id"])
    assert svc.get_strategy(a["strategy_id"])["status"] == "armed"
    assert svc.get_strategy(b["strategy_id"])["status"] == "armed"


def test_paused_by_guardrail_version_also_gives_way(clean):
    """被熔断停机的旧版本也要让位 —— 否则它一恢复就和新版本打架。"""
    base = _mk()
    svc.arm(base["strategy_id"])
    s = get_session()
    try:
        row = s.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == base["strategy_id"]).first()
        row.status = "paused_by_guardrail"
        s.commit()
    finally:
        s.close()
    f = _fork(base["strategy_id"], name="v2")
    assert svc.arm(f["strategy_id"])["superseded"] == [base["strategy_id"]]


# ── 3. 软退役 ─────────────────────────────────────────────────────────────

def test_retire_keeps_runs_and_drops_pending(clean):
    """🔴 回归：退役不许失忆。"""
    base = _mk()
    sid = base["strategy_id"]
    _mk_run(sid)
    _mk_run(sid, minutes_ago=60)
    s = get_session()
    try:
        s.add(CryptoPendingOrder(order_ref="CPO-T1", strategy_id=sid, symbol="BTCUSDT.BN",
                                 side="BUY", quantity=0.001, status="PENDING"))
        s.commit()
    finally:
        s.close()

    out = svc.retire(sid)
    assert out["status"] == "retired" and out["enabled"] is False
    assert out["pending_orders_dropped"] == 1     # 待确认单必须清，否则会误成交

    s = get_session()
    try:
        assert s.query(CryptoStrategyRun).filter(
            CryptoStrategyRun.strategy_id == sid).count() == 2, "退役把战绩删了"
        assert s.query(CryptoPendingOrder).filter(
            CryptoPendingOrder.strategy_id == sid).count() == 0
        assert s.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == sid).count() == 1, "退役把策略行删了"
    finally:
        s.close()


def test_retired_strategy_still_has_performance(clean):
    """退役之后 `get_strategy_performance` 仍算得出东西 —— 这才叫「归档」。"""
    from crypto_strategy.performance import strategy_health
    base = _mk()
    _mk_run(base["strategy_id"])
    svc.retire(base["strategy_id"])
    h = strategy_health(base["strategy_id"], days=30)
    assert h["ok"] is True and h["runs"]["total"] == 1
    assert h["status"] == "retired"


def test_delete_strategy_is_the_explicit_destructive_path(clean):
    base = _mk()
    _mk_run(base["strategy_id"])
    out = svc.delete_strategy(base["strategy_id"])
    assert out["deleted"] is True and out["runs_deleted"] == 1
    with pytest.raises(StrategyError):
        svc.get_strategy(base["strategy_id"])


# ── 4. 存量迁移 ───────────────────────────────────────────────────────────

def test_legacy_rows_backfill_to_own_family_v1(tmp_path):
    """存量行 = 每条策略自成一族的第一版。此前根本没有「多版本」这个概念，回填无损。

    ⚠️ 造存量行必须用裸 SQL 打回 NULL：ORM 的 default 对 `version=None` 也会生效。
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from data_engine.storage.models import Base

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(CryptoStrategy(strategy_id="CS-OLD-1", name="老策略",
                         universe=json.dumps({"symbols": ["BTCUSDT.BN"]})))
    s.commit()
    s.close()
    with eng.begin() as conn:
        conn.execute(text("UPDATE crypto_strategies SET family_id=NULL, version=NULL"))
        conn.execute(text("UPDATE crypto_strategies SET family_id = strategy_id "
                          "WHERE family_id IS NULL"))
        conn.execute(text("UPDATE crypto_strategies SET version = 1 WHERE version IS NULL"))
        row = conn.execute(text(
            "SELECT family_id, version FROM crypto_strategies")).fetchone()
    assert row == ("CS-OLD-1", 1)


def test_init_db_actually_runs_the_backfill():
    """别测了个只活在测试里的 SQL。"""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data_engine", "storage", "database.py")
    src = open(path, encoding="utf-8").read()
    assert "SET family_id = strategy_id" in src
    assert "idx_crypto_strategy_family_version" in src


# ── 5. 复审补的回归 ───────────────────────────────────────────────────────

def test_latest_only_never_hides_a_running_version(clean):
    """🔴 回归：v1 armed 正在排真单、v2 还是 draft 时，**v1 不许从列表里消失**。

    只按最大版号取当前版，会让 Jason 在 App 里看到一条未启用的草稿，
    而那条真在下单的 v1 看不到、pause 不了、retire 不了。
    """
    base = _mk()
    svc.arm(base["strategy_id"])
    f = _fork(base["strategy_id"], name="v2")          # draft，未启用
    ids = {s["strategy_id"] for s in svc.list_strategies()}
    assert ids == {base["strategy_id"], f["strategy_id"]}, "在跑的 v1 被藏起来了"


def test_enable_paper_also_supersedes_siblings(clean):
    """🔴 回归：enable_paper 也要让位，否则 v1(live) 和 v2(paper) 同族并存、引擎两条都跑。"""
    base = _mk()
    svc.arm(base["strategy_id"])
    f = _fork(base["strategy_id"], name="v2")
    res = svc.enable_paper(f["strategy_id"])
    assert res["superseded"] == [base["strategy_id"]]
    live = [s for s in svc.list_strategies(latest_only=False) if s["enabled"]]
    assert [s["strategy_id"] for s in live] == [f["strategy_id"]]


def test_retired_strategy_cannot_be_revived_by_arm(clean):
    """退役是 Jason 的决定，不该被一次上线动作推翻。"""
    base = _mk()
    svc.retire(base["strategy_id"])
    with pytest.raises(StrategyError):
        svc.arm(base["strategy_id"])
    with pytest.raises(StrategyError):
        svc.enable_paper(base["strategy_id"])


def test_retire_keeps_in_flight_orders(clean):
    """🔴 回归：`EXECUTING` / `STALE` 的单**绝不能删**。

    它们可能已经在币安成交了；删掉之后 `_finalize()` 找不到行静默 no-op、
    `cleanup()` 也再没机会标 STALE 发对账告警 —— 本地线索归零。
    """
    base = _mk()
    sid = base["strategy_id"]
    s = get_session()
    try:
        for ref, st in (("CPO-P", "PENDING"), ("CPO-E", "EXECUTING"), ("CPO-S", "STALE")):
            s.add(CryptoPendingOrder(order_ref=ref, strategy_id=sid, symbol="BTCUSDT.BN",
                                     side="BUY", quantity=0.001, status=st))
        s.commit()
    finally:
        s.close()

    out = svc.retire(sid)
    assert out["pending_orders_dropped"] == 1              # 只删了 PENDING
    assert set(out["pending_orders_kept"]) == {"CPO-E", "CPO-S"}
    assert "可能已经在币安成交" in out["warning"]

    s = get_session()
    try:
        left = {r.order_ref for r in s.query(CryptoPendingOrder).filter(
            CryptoPendingOrder.strategy_id == sid).all()}
        assert left == {"CPO-E", "CPO-S"}
    finally:
        s.close()


def test_fork_records_lineage(clean):
    base = _mk()
    f = _fork(base["strategy_id"], name="v2")
    assert svc.get_strategy(f["strategy_id"])["forked_from"] == base["strategy_id"]


def test_arm_still_works_when_family_id_is_null(clean):
    """🔴 兜底：某个插入点忘了写 `family_id` 时，让位查询不能空转。

    空转的后果是 v1 和 v2 同时 armed + live，而 `pending.has_open` 的去重是按
    `strategy_id` 的，**拦不住跨版本对同一个币重复下单**。

    真实情形是「存量/新插入点留了 NULL，然后在它上面 fork」：fork 时 family 回落成
    base 的 `strategy_id`，所以让位查询必须同时认 `strategy_id == family` 这一支。
    ⚠️ 造 NULL 必须用裸 SQL：ORM 对 `family_id=None` 会走 default。
    """
    from sqlalchemy import text
    base = _mk()
    s = get_session()
    try:
        s.execute(text("UPDATE crypto_strategies SET family_id = NULL "
                       "WHERE strategy_id = :sid"), {"sid": base["strategy_id"]})
        s.commit()
    finally:
        s.close()
    svc.arm(base["strategy_id"])
    f = _fork(base["strategy_id"], name="v2")     # family 回落成 base 的 strategy_id
    assert f["family_id"] == base["strategy_id"]
    assert svc.arm(f["strategy_id"])["superseded"] == [base["strategy_id"]]


def test_init_db_migration_runs_on_a_table_without_the_new_columns(tmp_path, monkeypatch):
    """🔴 真跑 `init_db()` 的 ALTER 分支 —— 别只 grep 源码字符串。

    根 conftest 用 `create_all` 建库，列天生就有，所以那条迁移在门禁里**从没被执行过**。
    这里手工建一张缺列的老表，再让 init_db 去补。
    """
    from sqlalchemy import create_engine, inspect, text

    from common.db import make_session_factory
    from data_engine.storage import database as db

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE crypto_strategies ("
            " id INTEGER PRIMARY KEY, strategy_id VARCHAR(40), name VARCHAR(100),"
            " enabled INTEGER, mode VARCHAR(10), status VARCHAR(24))"))
        conn.execute(text("INSERT INTO crypto_strategies"
                          " (strategy_id, name, enabled, mode, status)"
                          " VALUES ('CS-OLD', '老策略', 0, 'paper', 'draft')"))

    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", make_session_factory(eng))
    db.init_db()
    db.init_db()                                   # 幂等

    cols = {c["name"] for c in inspect(eng).get_columns("crypto_strategies")}
    assert {"family_id", "version", "forked_from"} <= cols
    with eng.begin() as conn:
        row = conn.execute(text("SELECT family_id, version FROM crypto_strategies")).fetchone()
        idx = {r[1] for r in conn.execute(text("PRAGMA index_list(crypto_strategies)"))}
    assert row == ("CS-OLD", 1)
    assert "idx_crypto_strategy_family_version" in idx
