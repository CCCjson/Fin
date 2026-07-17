"""`decision_log.backfill_outcomes()` 的回填编排测试。

⚠️ **全程用内存库，绝不碰生产 `data/market.db`。**（历史事故：延迟 import 的写库
调用绕过 mock，把 6 行假推荐写进了生产库。）防护手法：patch **被测模块里的
`get_session` 名字** —— 不是 `database.get_session`。`decision_log.py` 顶部是
`from ... import get_session`，模块级绑定，所以要 patch `decision_log.get_session`。
"""
import os
import sys
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import decision_log  # noqa: E402
from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import DailyQuote, DecisionLog  # noqa: E402


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(decision_log, "get_session", lambda: Session())
    return Session


def _seed_decision(s, *, symbol="600519.SH", action="BUY", entry=100.0,
                   stop=None, target=None, days_ago=30, source="moneybill_recommend"):
    row = DecisionLog(
        decision_id=f"d{datetime.now().timestamp()}{symbol}{days_ago}{action}",
        created_at=datetime.now() - timedelta(days=days_ago),
        source=source, symbol=symbol, action=action, recommendation=action,
        entry_price=entry, stop_loss=stop, take_profit=target,
    )
    s.add(row)
    return row


def _seed_quotes(s, symbol="600519.SH", *, n=25, start_days_ago=29, close=110.0,
                 high=None, low=None):
    """在建议日之后铺 n 根日线。"""
    for i in range(n):
        d = date.today() - timedelta(days=start_days_ago - i)
        s.add(DailyQuote(
            symbol=symbol, market="a_share", date=d,
            open=close, high=high or close, low=low or close, close=close, volume=1000,
        ))


def test_evaluates_and_is_incremental(db):
    """跑两遍：第二遍 completed 不该再增长（completed 被 filter 天然滤掉）。"""
    s = db()
    _seed_decision(s)
    _seed_quotes(s)
    s.commit()
    s.close()

    first = decision_log.backfill_outcomes()
    assert first["total"] == 1 and first["completed"] == 1

    second = decision_log.backfill_outcomes()
    assert second["total"] == 0, "completed 的行不该再被扫"
    assert second["completed"] == 0


def test_retryable_unable_becomes_completed_tomorrow(db):
    """**这是「可重试」二分存在的全部理由。**

    今天没行情 → unable/no_quotes；明天行情补上了 → 重跑 → completed。
    如果不区分可重试，这条要么永远评不出来，要么每天白扫一遍。
    """
    s = db()
    _seed_decision(s, symbol="000001.SZ")
    s.commit()
    s.close()

    r1 = decision_log.backfill_outcomes()
    assert r1["unable"] == 1

    s = db()
    row = s.query(DecisionLog).first()
    assert row.outcome_status == "unable"
    assert row.unable_reason == "no_quotes"
    _seed_quotes(s, "000001.SZ")          # 行情补上了
    s.commit()
    s.close()

    r2 = decision_log.backfill_outcomes()
    assert r2["total"] == 1, "可重试的 unable 必须被重新扫到"
    assert r2["completed"] == 1


def test_non_retryable_unable_is_never_rescanned(db):
    """advisor 那种没记 action 的，评过一次就永久终结，别每天白扫。"""
    s = db()
    _seed_decision(s, action=None, source="advisor")
    s.commit()
    s.close()

    r1 = decision_log.backfill_outcomes()
    assert r1["unable"] == 1

    s = db()
    assert s.query(DecisionLog).first().unable_reason == "no_action"
    s.close()

    r2 = decision_log.backfill_outcomes()
    assert r2["total"] == 0, "不可重试的 unable 不该再被扫"


def test_original_decision_is_immutable(db):
    """**原始决策不可篡改** —— 否则复盘就是自欺欺人。

    回填只许写 outcome 列；改了当时的止损再去算胜率 = 给自己发奖状。
    """
    s = db()
    _seed_decision(s, entry=100.0, stop=88.0, target=120.0)
    _seed_quotes(s)
    s.commit()
    created_before = s.query(DecisionLog).first().created_at
    s.close()

    decision_log.backfill_outcomes()

    s = db()
    row = s.query(DecisionLog).first()
    assert row.entry_price == 100.0
    assert row.stop_loss == 88.0, "回填绝不许动原始建议的止损"
    assert row.take_profit == 120.0
    assert row.action == "BUY"
    assert row.created_at == created_before
    assert row.outcome_status == "completed"   # 但 outcome 列该写的写了
    assert row.engine_version == decision_log.ENGINE_VERSION
    assert row.evaluated_at is not None
    s.close()


def test_write_and_immutable_field_sets_are_disjoint_and_total():
    """新加列必须显式归类，不许漏网。

    白名单优于黑名单，但白名单也会漏 —— 这条测试保证「加了列却谁都没登记」会红。
    """
    write = decision_log._OUTCOME_WRITE_FIELDS
    frozen = decision_log._IMMUTABLE_REFRESH_FIELDS
    assert write & frozen == set(), "同一列不能既可写又不可变"

    all_cols = {c.name for c in DecisionLog.__table__.columns}
    unclassified = all_cols - write - frozen
    assert unclassified == set(), f"这些列没归类，回填该不该写它们？{unclassified}"


def test_pending_when_bars_not_enough_yet(db):
    """建议刚发几天，5 日窗口够了但 20 日没满 → pending，明天继续。"""
    s = db()
    _seed_decision(s, days_ago=8)
    _seed_quotes(s, n=8, start_days_ago=7)
    s.commit()
    s.close()

    r = decision_log.backfill_outcomes()
    assert r["pending"] == 1

    s = db()
    row = s.query(DecisionLog).first()
    assert row.outcome_5d is not None
    assert row.outcome_20d is None, "20 日窗口没满必须显式留 None"
    s.close()

    # pending 的行明天还要接着扫
    assert decision_log.backfill_outcomes()["total"] == 1
