"""P0-4 批次2 的门禁 —— `DecisionLog.action` 全库只许有一种大小写。

背景（2026-07-27 查生产库）：`action` 列同时存着 `BUY`(69) 和 `buy`(6)，而消费方
**全是精确匹配**：

    report_engine/picks_log.py:98   DecisionLog.action == "BUY"
    decision_log.query_decisions    DecisionLog.action == action

小写行在它们眼里**根本不存在** —— 而且是静默的：查回 0 条会被读成「历史上没推荐过」，
不会报错。今天没咬到人纯属运气（6 条小写全在 crypto 的 execution 行上，而 picks_log
查的是 report_picks）。

治法是**单一收口点**：归一收在 `record_decision` 一处 + 存量一条 SQL 迁移，
**不去逐个改调用方**（小写就是 crypto 那边原样透传币安的 `side` 来的）——
靠每个写入点自觉正是 P0-4 这张卡在治的病。

⚠️ 全程用临时库，绝不碰生产 `data/market.db`（见 `tests/conftest.py` 文首）。
"""
import os
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import decision_log  # noqa: E402
from common.decision_kind import ADVICE, EXECUTION  # noqa: E402
from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import DecisionLog  # noqa: E402


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(decision_log, "get_session", lambda: session_factory())
    return session_factory


# ── 1. 写入期归一 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("given,want", [
    ("buy", "BUY"),
    ("  Buy  ", "BUY"),
    ("BUY", "BUY"),
    ("sell", "SELL"),
    ("", None),          # 空串归 None，不留一个假的空字符串在库里
    ("   ", None),
    (None, None),
])
def test_record_decision_normalizes_action(db, given, want):
    """`record_decision` 是唯一收口点 —— 从哪个调用方来的都归一。"""
    did = decision_log.record_decision(
        source="crypto", entry_kind=EXECUTION, symbol="BTCUSDT.BN",
        action=given, entry_price=65000.0,
    )
    assert did, "留痕失败了（record_decision 吞异常返 None）"
    s = db()
    row = s.query(DecisionLog).filter(DecisionLog.decision_id == did).one()
    assert row.action == want
    s.close()


def test_query_by_lowercase_action_still_finds_rows(db):
    """查询入参也归一：传 `action="buy"` 必须查得到 `BUY` 的行。

    不归一的话返回**空结果而不是报错** —— 比报错糟得多，会被 MoneyBill 读成
    「历史上没推荐过」。
    """
    decision_log.record_decision(source="report_picks", entry_kind=ADVICE,
                                 symbol="600519.SH", action="BUY", entry_price=1650.0)
    assert decision_log.query_decisions(action="buy")["total"] == 1
    assert decision_log.query_decisions(action="  BuY ")["total"] == 1
    assert decision_log.query_decisions(action="SELL")["total"] == 0


# ── 2. 存量迁移（database.init_db）────────────────────────────────────────────

def test_migration_upcases_legacy_action_and_is_idempotent(tmp_path, monkeypatch):
    """存量小写归一 + **幂等** + 不碰 NULL。

    幂等靠 `WHERE action <> upper(trim(action))` 天然成立（改完条件就不再命中），
    不靠「跑过一次就别再跑」的外部约定 —— 迁移每次启动都会跑。
    """
    from data_engine.storage import database as db

    eng = create_engine(f"sqlite:///{tmp_path / 'case.db'}")
    Base.metadata.create_all(eng)
    make = sessionmaker(bind=eng)
    s = make()
    rows = [
        ("lower", "buy", "BUY"),
        ("mixed", " Sell ", "SELL"),
        ("already", "BUY", "BUY"),      # 已经大写：迁移不该动它
        ("nulled", None, None),         # NULL：迁移不该把它变成空串
    ]
    for did, action, _want in rows:
        s.add(DecisionLog(decision_id=did, source="crypto", entry_kind=EXECUTION,
                          symbol="X", action=action, entry_price=1.0))
    s.commit()
    s.close()

    monkeypatch.setattr(db, "engine", eng)
    db.init_db()
    db.init_db()          # 跑两遍：幂等

    s = make()
    for did, given, want in rows:
        got = s.query(DecisionLog).filter(DecisionLog.decision_id == did).one()
        assert got.action == want, f"action={given!r} 归一错了：{got.action!r} != {want!r}"
    s.close()

    # 不变式：迁移跑完，全库不许再有非规范大小写的 action。
    with eng.connect() as conn:
        dirty = conn.execute(text(
            "SELECT count(*) FROM decision_logs "
            "WHERE action IS NOT NULL AND action <> upper(trim(action))"
        )).scalar()
    assert dirty == 0, f"迁移后仍有 {dirty} 行 action 大小写不规范"
