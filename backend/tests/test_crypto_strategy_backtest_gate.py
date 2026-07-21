"""需求3 阶段2：回测闸 + service arm 武装闸（内存库，mock C++ 回测）。"""
import pytest

from crypto_intel_engine.dsl import (
    Condition,
    ConditionGroup,
    CostModel,
    CryptoStrategySpec,
    EntryRules,
    ExitRules,
    Guardrails,
    Universe,
)
from crypto_strategy.backtest_gate import run_backtest_gate


@pytest.fixture
def mem_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from data_engine.storage import database as db
    from data_engine.storage.models import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "get_session", lambda: make())
    return make


def _spec(**over):
    base = dict(
        name="t", strategy_kind="swing",
        universe=Universe(symbols=["BTCUSDT.BN"]),
        entry_rules=EntryRules(when=ConditionGroup(
            all_of=[Condition(field="funding_rate", op="lt", value=0),
                    Condition(field="composite", op="gte", value=60)])),
        exit_rules=ExitRules(when=ConditionGroup(
            all_of=[Condition(field="composite", op="lt", value=40)])),
        cost_model=CostModel(),
        guardrails=Guardrails(per_order_notional_usdt=30, max_orders_per_day=6),
    )
    base.update(over)
    return CryptoStrategySpec(**base)


def _bars(n=60):
    return [{"date": f"2026-01-{i % 28 + 1:02d}", "open": 100 + i, "high": 101 + i,
             "low": 99 + i, "close": 100 + i, "volume": 1000} for i in range(n)]


def _fake_collect(on_bar, df):
    return [{"date": "2026-01-05", "action": "buy"}, {"date": "2026-01-20", "action": "sell"}]


# ── 回测闸 ──

def test_gate_net_positive_passes():
    def run_bt(bars, signals, **kw):
        return {"metrics": {"total_return": 0.12, "total_trades": 4, "sharpe_ratio": 1.1}}
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars()}, run_bt=run_bt, collect=_fake_collect)
    assert r["passed"] is True
    assert r["net_return"] == pytest.approx(0.12)
    assert r["degraded"] is True                       # v1 一律标降级
    assert any("funding_rate" in x for x in r["degraded_reasons"])   # 非技术原语被点名


def test_gate_net_negative_blocked():
    def run_bt(bars, signals, **kw):
        return {"metrics": {"total_return": -0.05, "total_trades": 30}}   # 毛赚净亏典型
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars()}, run_bt=run_bt, collect=_fake_collect)
    assert r["passed"] is False


def test_gate_uses_strategy_slippage():
    seen = {}
    def run_bt(bars, signals, **kw):
        seen.update(kw)
        return {"metrics": {"total_return": 0.01}}
    spec = _spec(cost_model=CostModel(slippage_pct=0.002))
    run_backtest_gate(spec, {"BTCUSDT.BN": _bars()}, run_bt=run_bt, collect=_fake_collect)
    assert seen["slippage_pct"] == 0.002                # 策略自己的滑点被传进回测


def test_gate_short_history_skipped():
    def run_bt(bars, signals, **kw):  # 不该被调用
        raise AssertionError("历史不足不应回测")
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars(5)}, run_bt=run_bt, collect=_fake_collect)
    assert r["net_return"] is None and r["passed"] is False


# ── service：编译落库 + arm 武装闸 ──

def test_compile_persist_and_arm(mem_db, monkeypatch):
    from crypto_strategy.service import crypto_strategy_service as svc

    # mock 回测为净正（避开 C++/DataEngine）
    monkeypatch.setattr(svc, "backtest",
                        lambda spec: {"passed": True, "net_return": 0.2,
                                      "metrics": {}, "degraded": True, "degraded_reasons": [],
                                      "per_symbol": []})
    res = svc.compile_and_persist(_spec(), description_nl="牛市抄底")
    sid = res["strategy_id"]
    assert res["summary"]["status"] == "backtested"
    assert res["summary"]["backtest_passed"] is True

    armed = svc.arm(sid)
    assert armed["mode"] == "live" and armed["enabled"] is True and armed["status"] == "armed"


def test_arm_no_longer_needs_backtest(mem_db, monkeypatch):
    """半自动系统：arm 不卡回测（安全靠逐笔确认+护栏+风控）。回测负也能上实盘。"""
    from crypto_strategy.service import crypto_strategy_service as svc

    monkeypatch.setattr(svc, "backtest",
                        lambda spec: {"passed": False, "net_return": -0.1,
                                      "metrics": {}, "degraded": True, "degraded_reasons": [],
                                      "per_symbol": []})
    res = svc.compile_and_persist(_spec())
    armed = svc.arm(res["strategy_id"])       # 回测未过也 arm 成功
    assert armed["mode"] == "live" and armed["enabled"] is True and armed["status"] == "armed"


def test_paper_enable_needs_no_backtest(mem_db, monkeypatch):
    from crypto_strategy.service import crypto_strategy_service as svc
    monkeypatch.setattr(svc, "backtest",
                        lambda spec: {"passed": False, "net_return": -0.1, "metrics": {},
                                      "degraded": True, "degraded_reasons": [], "per_symbol": []})
    res = svc.compile_and_persist(_spec())
    enabled = svc.enable_paper(res["strategy_id"])   # 纸面不需回测通过
    assert enabled["mode"] == "paper" and enabled["enabled"] is True


def test_spec_roundtrip_through_db(mem_db, monkeypatch):
    from crypto_strategy.service import crypto_strategy_service as svc
    from crypto_strategy.service import spec_from_row
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy
    monkeypatch.setattr(svc, "backtest", lambda spec: {"passed": True, "net_return": 0.1,
                        "metrics": {}, "degraded": True, "degraded_reasons": [], "per_symbol": []})
    res = svc.compile_and_persist(_spec(strategy_kind="arb",
                                        cost_model=CostModel(min_net_edge_pct=0.01)))
    s = get_session()
    try:
        row = s.query(CryptoStrategy).filter_by(strategy_id=res["strategy_id"]).first()
        rebuilt = spec_from_row(row)
        assert rebuilt.strategy_kind == "arb"
        assert rebuilt.cost_model.min_net_edge_pct == 0.01
        assert rebuilt.universe.symbols == ["BTCUSDT.BN"]
    finally:
        s.close()
