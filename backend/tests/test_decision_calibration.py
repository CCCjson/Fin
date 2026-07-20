"""P0-3 置信度校准反哺 —— `decision_log.compute_calibration` 的口径测试。

⚠️ **全程内存库，绝不碰生产 `data/market.db`。** patch 被测模块里的 `get_session`
名字（`decision_log.get_session`），同 test_decision_outcome_backfill.py。

校准闭环的最后一环：拿一个 source 最近 window 条**已评**决策的真实命中率，反过来
调它下次输出的 confidence。样本不足（<30）恒为 1.0（不校准），只下调不上抬。
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import decision_log  # noqa: E402
from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import DecisionLog  # noqa: E402


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(decision_log, "get_session", lambda: session_factory())
    # 清掉 get_calibration_factor 的进程内 TTL 缓存，别让上个测试的因子串进来
    decision_log._CALIB_CACHE.clear()
    return session_factory


def _seed(s, *, n, outcome, confidence=70.0, source="cockpit", horizon_col="outcome_20d",
          base_days_ago=40):
    """塞 n 条已评决策（outcome 直接写死，不必跑 backfill）。"""
    for i in range(n):
        row = DecisionLog(
            decision_id=f"c{datetime.now().timestamp()}{source}{outcome}{confidence}{i}",
            created_at=datetime.now() - timedelta(days=base_days_ago, hours=i),
            source=source, symbol=f"6{i:05d}.SH", action="BUY", recommendation="BUY",
            confidence=confidence, outcome_status="completed",
        )
        setattr(row, horizon_col, outcome)
        s.add(row)


# ── _calibration_factor_from_accuracy：纯计算 ──

def test_factor_none_accuracy_is_one():
    assert decision_log._calibration_factor_from_accuracy(None) == 1.0


def test_factor_only_discounts_never_boosts():
    f = decision_log._calibration_factor_from_accuracy
    assert f(0.5) == 1.0            # 抛硬币水平 → 不动
    assert f(0.8) == 1.0            # 比抛硬币好 → **不上抬**（封在 1.0）
    assert f(0.4) == 0.9            # 差一点 → 打 9 折
    assert f(0.3) == 0.8
    assert f(0.0) == 0.5            # 再差也有下限（floor=0.5）


# ── compute_calibration：整合 ──

def test_inert_below_min_samples(db):
    """样本 < 30 → 不校准（factor=1.0），哪怕胜率很低。"""
    s = db()
    _seed(s, n=10, outcome="loss")     # 10 条全错，但样本不够
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit")
    assert r["total_samples"] == 10
    assert r["calibrated"] is False
    assert r["calibration_factor"] == 1.0


def test_discounts_when_history_is_poor(db):
    """>=30 样本 + 命中率 0.3 → factor 0.8（打折生效）。"""
    s = db()
    _seed(s, n=12, outcome="win")
    _seed(s, n=28, outcome="loss")     # 12/(12+28)=0.3
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit")
    assert r["total_samples"] == 40
    assert r["historical_accuracy"] == 0.3
    assert r["calibrated"] is True
    assert r["calibration_factor"] == 0.8


def test_does_not_boost_when_history_is_good(db):
    """命中率高（0.8）也**不上抬** confidence，factor 封在 1.0。"""
    s = db()
    _seed(s, n=32, outcome="win")
    _seed(s, n=8, outcome="loss")      # 0.8
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit")
    assert r["historical_accuracy"] == 0.8
    assert r["calibration_factor"] == 1.0


def test_neutral_is_not_a_win(db):
    """neutral（±1% 带内）不算命中，进分母不进分子。"""
    s = db()
    _seed(s, n=15, outcome="win")
    _seed(s, n=15, outcome="neutral")   # 15/30 = 0.5
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit")
    assert r["total_samples"] == 30
    assert r["historical_accuracy"] == 0.5


def test_window_limits_to_recent(db):
    """滚动窗口只看最近 window 条（老的不进）。"""
    s = db()
    _seed(s, n=20, outcome="win", base_days_ago=5)     # 最近的 20 条
    _seed(s, n=40, outcome="loss", base_days_ago=100)  # 更老的 40 条
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit", window=50)
    assert r["total_samples"] == 50, "只取最近 50 条"
    # 最近 20 win + 老的里较新的 30 loss（按 created_at desc）→ 20/50 = 0.4
    assert r["historical_accuracy"] == 0.4


def test_empty_source_is_inert(db):
    """没有任何该 source 的已评决策 → factor 1.0、命中率 None。"""
    s = db()
    _seed(s, n=5, outcome="win", source="advisor")   # 别的 source
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit")
    assert r["total_samples"] == 0
    assert r["historical_accuracy"] is None
    assert r["calibration_factor"] == 1.0


def test_buckets_report_per_confidence_accuracy(db):
    """buckets 回答「你说高置信度时实际准多少」（按 0-100 分桶，不是 0-1）。"""
    s = db()
    # 高置信度桶（85）：4 win / 6 loss = 0.4
    _seed(s, n=4, outcome="win", confidence=85.0)
    _seed(s, n=6, outcome="loss", confidence=85.0)
    # 中桶（65）：全对
    _seed(s, n=5, outcome="win", confidence=65.0)
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit")
    by = {b["bucket"]: b for b in r["buckets"]}
    assert by["80-100"]["count"] == 10
    assert by["80-100"]["actual_accuracy"] == 0.4      # 说得最满，其实只对 40%
    assert by["60-70"]["count"] == 5
    assert by["60-70"]["actual_accuracy"] == 1.0
    assert by["50-60"]["actual_accuracy"] is None      # 空桶返 None 不返 0.0


def test_only_judged_rows_count(db):
    """未评（outcome 为 None）的行不进校准分母。"""
    s = db()
    _seed(s, n=30, outcome="win")
    # 20 条还没评：confidence 有但 outcome_20d 为 None
    for i in range(20):
        s.add(DecisionLog(
            decision_id=f"pending{i}", created_at=datetime.now(),
            source="cockpit", symbol=f"9{i:05d}.SH", action="BUY",
            recommendation="BUY", confidence=90.0, outcome_status="pending",
        ))
    s.commit()
    s.close()

    r = decision_log.compute_calibration("cockpit")
    assert r["total_samples"] == 30, "未评的 20 条不算"


def test_get_calibration_factor_returns_factor_and_caches(db):
    """带缓存的瘦封装：返回因子，并把结果写进 TTL 缓存（批量选股不必每只查库）。"""
    s = db()
    _seed(s, n=12, outcome="win")
    _seed(s, n=28, outcome="loss")   # 0.3 → factor 0.8
    s.commit()
    s.close()

    assert decision_log.get_calibration_factor("cockpit") == 0.8
    # 第一次调用后缓存里就有这个 key，后续命中不再查库
    assert ("cockpit", decision_log._CALIBRATION_WINDOW, 20) in decision_log._CALIB_CACHE


def test_get_calibration_factor_fails_open(db, monkeypatch):
    """校准算不出来（异常）→ fail-open 返 1.0，绝不弄坏打分。"""
    def _boom(*a, **k):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(decision_log, "compute_calibration", _boom)
    assert decision_log.get_calibration_factor("cockpit") == 1.0
