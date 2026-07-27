"""门禁：成交的**来源归因**不许靠默认值兜底（S1）。

# 为什么

「我那条 BTC 策略赚了多少」在此之前算不出来 —— `pnl_realized_today` 是**整个账户**的
当日盈亏，策略的单、Jason 在 MoneyBill 下的单、待确认单成交全混在一个币安账户里。

而卡片设想的归因链路**实测是断的**：`crypto_strategy_runs.executed_order_ids` 存的是
待确认单的 `CPO-…` 引用（不是币安 orderId，模型 docstring 写错了），要经
`CryptoPendingOrder` 转一手 —— 而那张桥表的 FILLED 行 **24 小时后被 `cleanup()` 删掉**。
所以归因必须当场写进成交台账本身。

这张门禁钉的就是「当场写」这件事不会被忘：

1. `record_crypto_trade` 的 `source_kind` 是**关键字且无默认值** —— 忘传直接 TypeError
2. 全项目每个写入点都显式传了（AST 扫）
3. `strategy` 缺 `source_ref` 时**降级成 unknown 而不是硬记**（宁可诚实地说不知道，
   也不能给某条策略记一笔不属于它的钱 —— 这个数字要拿来决定切不切换策略）
4. 存量行回填成 `unknown`，幂等且永不覆盖
"""
import ast
import os

import pytest

from common import trade_source as ts

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. 真源本身 ───────────────────────────────────────────────────────────

def test_registry_self_consistent():
    assert ts.TRADE_SOURCES == {ts.STRATEGY, ts.AI_ADVICE, ts.MANUAL,
                                ts.RECONCILE, ts.UNKNOWN}
    assert ts.REF_REQUIRED <= ts.TRADE_SOURCES
    for k in ts.TRADE_SOURCES:
        assert ts.LABELS.get(k), f"{k} 缺中文说明"
        assert ts.label_of(k) == ts.LABELS[k]


@pytest.mark.parametrize("raw", [None, "", "  ", "strategy_x", "STRATEGY_X", "ai", "回执"])
def test_normalize_falls_back_to_unknown(raw):
    """⚠️ **方向与 `decision_kind.normalize()` 相反，别照抄。**

    那边回落到 `advice`（宁可多评一条噪声）；这边回落到 `unknown` —— 猜错的代价不对称：
    把一笔来路不明的成交算进某条策略 = **凭空捏造它的战绩**。
    """
    assert ts.normalize(raw) == ts.UNKNOWN


@pytest.mark.parametrize("raw,want", [
    ("strategy", ts.STRATEGY), ("  STRATEGY ", ts.STRATEGY),
    ("ai_advice", ts.AI_ADVICE), ("manual", ts.MANUAL), ("reconcile", ts.RECONCILE),
])
def test_normalize_accepts_known(raw, want):
    assert ts.normalize(raw) == want


def test_only_strategy_requires_a_ref():
    """`strategy` 不带 ref = 知道「来自某条策略」却不知道哪条 = 等于没归因。"""
    assert ts.needs_ref(ts.STRATEGY) is True
    for k in (ts.AI_ADVICE, ts.MANUAL, ts.RECONCILE, ts.UNKNOWN, None, "垃圾"):
        assert ts.needs_ref(k) is False


# ── 2. 写入口：无默认值 + 缺 ref 降级 ─────────────────────────────────────

def test_record_crypto_trade_requires_source_kind():
    """无默认值是刻意的：能靠默认兜底，下一个写入点就会忘。"""
    import inspect

    from crypto_intel_engine.execution import record_crypto_trade
    p = inspect.signature(record_crypto_trade).parameters["source_kind"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is inspect.Parameter.empty, (
        "source_kind 有默认值了 —— 归因一旦能兜底就一定会被忘，"
        "而忘掉的后果是某条策略的战绩里凭空多/少一笔钱。"
    )


def _last_trade():
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoTrade
    session = get_session()
    try:
        return session.query(CryptoTrade).order_by(CryptoTrade.id.desc()).first()
    finally:
        session.close()


@pytest.fixture
def clean_trades():
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoTrade
    yield
    session = get_session()
    try:
        session.query(CryptoTrade).delete()
        session.commit()
    finally:
        session.close()


def test_strategy_without_ref_degrades_to_unknown(clean_trades):
    from crypto_intel_engine.execution import record_crypto_trade

    record_crypto_trade("BTCUSDT.BN", "BUY", 65000.0, 0.001, "oid-1",
                        source_kind=ts.STRATEGY, source_ref=None)
    row = _last_trade()
    assert row is not None
    assert row.source_kind == ts.UNKNOWN, (
        "缺 ref 的策略单被硬记成了 strategy —— 那会变成「来自某条不知道哪条的策略」，"
        "比诚实地说「来源不明」更坏。"
    )


def test_strategy_with_ref_is_attributed(clean_trades):
    from crypto_intel_engine.execution import record_crypto_trade

    record_crypto_trade("BTCUSDT.BN", "BUY", 65000.0, 0.001, "oid-2",
                        source_kind=ts.STRATEGY, source_ref="CS-20260721191526-17cb24")
    row = _last_trade()
    assert row.source_kind == ts.STRATEGY
    assert row.source_ref == "CS-20260721191526-17cb24"


def test_garbage_kind_becomes_unknown_not_stored_raw(clean_trades):
    from crypto_intel_engine.execution import record_crypto_trade

    record_crypto_trade("ETHUSDT.BN", "SELL", 1900.0, 0.5, "oid-3", source_kind="乱写的")
    assert _last_trade().source_kind == ts.UNKNOWN


# ── 3. AST：每个写入点都显式传 ────────────────────────────────────────────

_TRADE_WRITE_SITES = {
    "crypto_strategy/pending.py": ts.STRATEGY,       # 策略排的单，Jason 确认后成交
    "agents/tools/crypto_tools.py": ts.AI_ADVICE,    # MoneyBill 对话下单
}


def _call_names(tree: ast.Module) -> set:
    """`record_crypto_trade` 在本文件里的所有可调用名字（含 `as` 别名）。

    ⚠️ 别名必须收：`crypto_tools.py` 就是
    `from ...execution import record_crypto_trade as _record_crypto_trade`。
    不收别名的门禁在 `decision_source` 那边实测能被完整绕过（十条全绿）。
    """
    names = {"record_crypto_trade"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == "record_crypto_trade" and a.asname:
                    names.add(a.asname)
    return names


def _collect_trade_write_sites():
    found = {}
    for root, _dirs, files in os.walk(_BACKEND):
        if any(p in root for p in (os.sep + "tests", os.sep + "__pycache__", os.sep + ".")):
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, _BACKEND).replace(os.sep, "/")
            if rel == "crypto_intel_engine/execution.py":
                continue                                # 定义处
            src = open(path, encoding="utf-8").read()
            if "record_crypto_trade" not in src:
                continue
            tree = ast.parse(src)
            names = _call_names(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if fname not in names:
                    continue
                kw = {k.arg for k in node.keywords}
                found.setdefault(rel, []).append((node.lineno, "source_kind" in kw))
    return found


def test_every_trade_write_site_passes_source_kind():
    found = _collect_trade_write_sites()
    assert set(found) == set(_TRADE_WRITE_SITES), (
        f"成交台账写入点变了。新增的：{sorted(set(found) - set(_TRADE_WRITE_SITES))}；"
        f"消失的：{sorted(set(_TRADE_WRITE_SITES) - set(found))}。"
        f"新写入点请先回答：这笔单是从哪儿来的（策略/对话/补录/对账）？"
    )
    missing = [(f, ln) for f, calls in found.items() for ln, ok in calls if not ok]
    assert not missing, (
        f"这些写入点没显式传 source_kind：{missing}。"
        f"⛔ 别指望默认值 —— 这个参数刻意没有默认值，靠它兜底正是本卡在治的病。"
    )


# ── 4. 存量迁移 ───────────────────────────────────────────────────────────

def test_migration_tags_legacy_rows_and_is_idempotent(tmp_path):
    """存量行标 `unknown`，幂等 + **永不覆盖**写入点标好的值。

    ⚠️ 造「存量行」必须用裸 SQL 打回 NULL：ORM 的 `default="unknown"` 对
    `source_kind=None` 也会生效（SQLAlchemy 把 None 当「没给值」）—— 同 P0-4 那条坑。
    """
    from datetime import date

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from data_engine.storage.models import Base, CryptoTrade

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for i, (kind, ref) in enumerate([(None, None), (ts.STRATEGY, "CS-x")]):
        s.add(CryptoTrade(symbol="BTCUSDT.BN", side="BUY", price=1.0, quantity=1.0,
                          amount=1.0, order_id=f"o{i}", trade_date=date(2026, 7, 27),
                          source_kind=kind, source_ref=ref))
    s.commit()
    s.close()
    with eng.begin() as conn:
        conn.execute(text("UPDATE crypto_trades SET source_kind = NULL WHERE order_id = 'o0'"))

    sql = "UPDATE crypto_trades SET source_kind = 'unknown' WHERE source_kind IS NULL"
    with eng.begin() as conn:
        assert conn.execute(text(sql)).rowcount == 1
    with eng.begin() as conn:
        assert conn.execute(text(sql)).rowcount == 0        # 幂等
        rows = dict(conn.execute(text(
            "SELECT order_id, source_kind FROM crypto_trades")).fetchall())
    assert rows == {"o0": ts.UNKNOWN, "o1": ts.STRATEGY}     # 永不覆盖


def test_init_db_actually_runs_that_migration():
    """上面那条 SQL 必须真的在 `init_db` 里 —— 别测了个只活在测试里的字符串。"""
    src = open(os.path.join(_BACKEND, "data_engine", "storage", "database.py"),
               encoding="utf-8").read()
    assert "crypto_trades" in src and "source_kind" in src
    assert "SET source_kind = 'unknown' WHERE source_kind IS NULL" in src
