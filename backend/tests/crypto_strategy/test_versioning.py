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
    CryptoArenaSwitch,
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
        # ⚠️ `CryptoArenaSwitch` 必须一起清：冷却期是**全局**查最近一条切换，
        # 留着会让下一个用例的冷却期从上一个用例的切换开始算 —— 用例之间串味。
        for m in (CryptoStrategyRun, CryptoPendingOrder, CryptoStrategyProposal,
                  CryptoArenaSwitch, CryptoStrategy):
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


def test_arming_live_supersedes_across_families_too(clean):
    """🔴 裁决 7「同期只有一条 live」是**全局**的，不是每族一条。

    这条测试原本断言的是相反的行为（「arm 乙不影响甲」），而那正是复审实测出来的
    bug：跨家族切换会留下**两条同时 armed+live+enabled**，
    `pending.has_open` 的去重按 strategy_id，两条会对同一个币各排一张单。
    """
    a = _mk("甲")
    b = _mk("乙")
    svc.arm(a["strategy_id"])
    res = svc.arm(b["strategy_id"])
    assert res["superseded"] == [a["strategy_id"]]
    assert svc.get_strategy(a["strategy_id"])["status"] == "superseded"
    assert svc.get_strategy(b["strategy_id"])["status"] == "armed"
    live = [s for s in svc.list_strategies(latest_only=False)
            if s["enabled"] and s["mode"] == "live"]
    assert len(live) == 1


def test_enable_paper_does_not_touch_other_families(clean):
    """反过来：paper 池是**多条并存**的挑战者池，enable_paper 只让同族让位。"""
    a = _mk("甲")
    b = _mk("乙")
    svc.enable_paper(a["strategy_id"])
    res = svc.enable_paper(b["strategy_id"])
    assert res["superseded"] == []
    assert svc.get_strategy(a["strategy_id"])["status"] == "armed"


def test_cross_family_switch_is_recorded_for_cooldown(clean):
    """🔴 冷却期建在切换留痕上 —— 跨家族切换**必须**留痕，否则「防反复横跳」形同虚设。

    （AI 每次都能造一条全新家族的策略，跨族恰恰是最容易横跳的场景。）
    """
    from data_engine.storage.models import CryptoArenaSwitch
    a = _mk("甲")
    b = _mk("乙")
    svc.arm(a["strategy_id"])
    svc.arm(b["strategy_id"])
    s = get_session()
    try:
        rows = s.query(CryptoArenaSwitch).all()
        assert len(rows) == 1
        assert (rows[0].from_strategy_id, rows[0].to_strategy_id) == \
            (a["strategy_id"], b["strategy_id"])
    finally:
        s.close()

    from crypto_strategy.arena import _days_since_last_switch
    assert _days_since_last_switch() == 0        # 不再是 None → 冷却期真的生效


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


# ── 组合策略的持久化（S8 批次3）────────────────────────────────────────────

def _combo_spec():
    sub = lambda n, w, syms: {                                    # noqa: E731
        "name": n, "weight": w, "universe": {"symbols": syms},
        "entry_rules": {"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
        "exit_rules": {"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}}}
    return {**_SPEC, "name": "组合",
            "universe": {"symbols": ["BTCUSDT.BN", "ETHUSDT.BN"]},
            "entry_rules": None, "exit_rules": None,
            "sub_strategies": [sub("趋势", 0.6, ["BTCUSDT.BN"]),
                               sub("均值回归", 0.4, ["ETHUSDT.BN"])]}


def test_combo_strategy_survives_a_round_trip_through_the_db(clean):
    """🔴 组合策略必须**存得下也读得回**。

    ⚠️ 差点漏掉：`_spec_to_columns` 原本无条件 `getattr(spec, key).model_dump_json()`，
    而组合策略的 `entry_rules` 是 **None** —— 落库那一刻就 AttributeError。
    而且 `sub_strategies` 当时根本没有对应的列，权重会**静默丢失**：
    存进去是组合策略，读回来变成一条没有规则的空壳。
    """
    from crypto_intel_engine.dsl import CryptoStrategySpec
    from crypto_strategy.service import crypto_strategy_service as svc

    spec = CryptoStrategySpec(**_combo_spec())
    r = svc.compile_and_persist(spec, do_backtest=False)

    from crypto_strategy.service import spec_from_row
    s = get_session()
    try:
        row = s.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == r["strategy_id"]).first()
        assert row.sub_strategies, "sub_strategies 没落库 —— 权重会静默丢失"
        assert row.entry_rules is None, "组合策略的顶层规则列该是 NULL"
        back = spec_from_row(row)
    finally:
        s.close()

    assert [(x.name, x.weight) for x in back.rule_sets()] == [("趋势", 0.6), ("均值回归", 0.4)]
    assert back.entry_rules is None, "组合策略不该有顶层规则"
    assert back.owner_of("ETHUSDT.BN").name == "均值回归"


def test_market_survives_a_round_trip_through_the_db(clean):
    """🔴 `market` 必须落库 —— 跟 `sub_strategies` 是**同一个坑**。

    漏了的话「存进去是股票策略、读回来是 crypto」，于是所有 DSL 条件都取不到值而
    **静默永不触发**：策略一单不下，而每一层看上去都正常。
    """
    from crypto_intel_engine.dsl import CryptoStrategySpec
    from crypto_strategy.service import crypto_strategy_service as svc
    from crypto_strategy.service import spec_from_row

    spec = CryptoStrategySpec(
        name="A股策略", market="a_share",
        universe={"symbols": ["600519.SH"]},
        entry_rules={"when": {"all_of": [
            {"field": "valuation.pe_ttm", "op": "lt", "value": 20}]}},
        exit_rules={"when": {"all_of": [
            {"field": "composite", "op": "lt", "value": 40}]}},
        guardrails=_SPEC["guardrails"])
    r = svc.compile_and_persist(spec, do_backtest=False)

    s = get_session()
    try:
        row = s.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == r["strategy_id"]).first()
        assert row.market == "a_share", "market 没落库"
        back = spec_from_row(row)
    finally:
        s.close()
    assert back.market == "a_share"
    # 读回来之后字段表也要跟着对 —— 否则条件会静默取不到值
    assert back.owner_of("600519.SH") is not None


# ── 任务 10：股票没接真券商时不许 arm 到 live ────────────────────────────────
#
# 🔴 守的是**数据真伪，不是资金安全**：`scheduler._adapter_for` 现在永远发
#    `PaperBroker`，一条 arm 到 live 的股票策略，成交是纸面的、却会以
#    `mode="live"` 落进台账 —— S4 的提案打分会拿它当真钱证据。


def _stock_spec(**over):
    from crypto_intel_engine.dsl import CryptoStrategySpec

    kw = dict(
        name="A股策略", market="a_share",
        universe={"symbols": ["600519.SH"]},
        entry_rules={"when": {"all_of": [
            {"field": "valuation.pe_ttm", "op": "lt", "value": 20}]}},
        exit_rules={"when": {"all_of": [
            {"field": "composite", "op": "lt", "value": 40}]}},
        guardrails=_SPEC["guardrails"])
    kw.update(over)
    return CryptoStrategySpec(**kw)


def _stock_strategy() -> dict:
    return svc.compile_and_persist(_stock_spec(), do_backtest=False)


def test_stock_cannot_be_armed_to_live_without_a_real_broker(clean):
    """🔴 拒绝，而且**说得出为什么**和**该怎么办**。"""
    r = _stock_strategy()
    with pytest.raises(StrategyError) as ei:
        svc.arm(r["strategy_id"])
    msg = str(ei.value)
    assert "真券商" in msg, "没说清是缺券商"
    assert "enable_paper" in msg or "纸面启用" in msg, "拒了却没告诉人该走哪条路"


def test_the_gate_fires_before_any_write(clean, monkeypatch):
    """🔴 闸必须在**任何写动作之前**。

    `arm` 里 `_supersede_siblings` 会把同族其它版本停跑 —— 闸开晚一步就变成
    「上线失败，但把正在跑的那条顺手停了」。

    ⚠️ **不能靠「查库看看有没有被改」来测这件事**：`arm` 的 `finally: session.close()`
    之前没有 commit，SQLAlchemy 会把未提交的 ORM 变更整个回滚 —— 于是把闸挪到
    `_supersede_siblings` **之后**（只要还在 commit 之前）那种查库式断言照样是绿的，
    读起来却像钉死了顺序。所以这里直接让退位动作**一被调用就炸**。
    """
    from crypto_strategy import service as svc_mod

    def _boom(*a, **kw):
        raise AssertionError("闸开晚了：退位动作在拒绝之前就跑了")

    monkeypatch.setattr(svc_mod, "_supersede_siblings", _boom)
    r = _stock_strategy()
    with pytest.raises(StrategyError):      # ⛔ 不是 AssertionError
        svc.arm(r["strategy_id"])


def test_paper_mode_is_still_allowed_for_stocks(clean):
    """⛔ 这道闸只挡 live —— 挡了 paper 的话股票策略就彻底没法跑了。"""
    r = _stock_strategy()
    out = svc.enable_paper(r["strategy_id"])
    assert out["mode"] == "paper"
    assert out["enabled"] == 1


def test_crypto_is_untouched_by_this_gate(clean):
    """crypto 走币安真券商，⛔ 别被这道闸误伤。

    ⚠️ 存量 crypto 策略的 `market` 列是 **NULL**，判据必须走
    `strategy_market()`（「NULL = crypto」的唯一实现）而不是裸读 `row.market`。
    """
    r = _mk()
    s = get_session()
    try:
        row = s.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == r["strategy_id"]).first()
        row.market = None            # 存量行的样子
        s.commit()
    finally:
        s.close()
    out = svc.arm(r["strategy_id"])
    assert out["mode"] == "live"


def test_the_gate_opens_when_a_real_broker_shows_up(clean, monkeypatch):
    """⛔ 判据不许写死成「股票永远拒绝」—— 那样接上券商也解不开。

    ⭐ 同一个 `live_broker_for` 也是调度器挑 broker 的出口，所以这条同时保证了
    「能不能上 live」和「实际用哪个 broker」不会各说各话。
    """
    import strategy_runtime.stock_adapter as sa

    monkeypatch.setattr(sa, "live_broker_for", lambda market: object())
    r = _stock_strategy()
    out = svc.arm(r["strategy_id"])
    assert out["mode"] == "live"
