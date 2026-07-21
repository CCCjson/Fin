"""需求3 阶段1：护栏纯函数（成本闸/频次/单笔/单币/当日熔断），离线。"""
import pytest

from crypto_intel_engine.dsl import CostModel, Guardrails
from crypto_strategy import guardrails as gr


@pytest.fixture
def crypto_engine_memory_db(monkeypatch):
    """内存 sqlite + 建全表，patch get_session（kill-switch 读写 UserSettings 用）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from data_engine.storage import database as db
    from data_engine.storage.models import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "get_session", lambda: make())
    return make


def _g(**kw):
    base = dict(per_order_notional_usdt=30, max_orders_per_day=6)
    base.update(kw)
    return Guardrails(**base)


# ── 成本净边际闸 ──

def test_cost_gate_blocks_when_net_negative():
    cm = CostModel(taker_fee_pct=0.001, slippage_pct=0.0005)   # 往返 0.0025
    ok, reason, net = gr.cost_gate(0.002, cm)                   # 毛 0.2% < 成本
    assert ok is False and net < 0
    assert "吃光" in reason


def test_cost_gate_blocks_below_min_edge():
    cm = CostModel(min_net_edge_pct=0.01)
    ok, _, _ = gr.cost_gate(0.008, cm)      # 净 0.55% < 要求 1%
    assert ok is False


def test_cost_gate_passes():
    cm = CostModel(min_net_edge_pct=0.005)
    ok, _, net = gr.cost_gate(0.02, cm)     # 毛2% - 0.25% = 1.75% ≥ 0.5%
    assert ok is True and net == pytest.approx(0.0175)


def test_cost_gate_none_edge_blocked():
    ok, _, net = gr.cost_gate(None, CostModel())
    assert ok is False and net is None


def test_take_profit_must_clear_cost():
    cm = CostModel(taker_fee_pct=0.001, slippage_pct=0.0005, min_net_edge_pct=0.005)
    ok, _ = gr.take_profit_clears_cost(100.0, 100.5, cm)   # 需 100*(1+0.0075)=100.75
    assert ok is False
    ok2, _ = gr.take_profit_clears_cost(100.0, 101.0, cm)
    assert ok2 is True


# ── 频次 / 费用 ──

def test_daily_counts_order_cap():
    ok, _ = gr.check_daily_counts(6, 0, 0.0, _g(max_orders_per_day=6))
    assert ok is False


def test_daily_counts_fee_cap():
    ok, _ = gr.check_daily_counts(1, 0, 3.0, _g(max_fees_per_day_usdt=3.0))
    assert ok is False


def test_daily_counts_round_trip_cap():
    ok, _ = gr.check_daily_counts(1, 4, 0.0, _g(max_round_trips_per_day=4))
    assert ok is False


def test_daily_counts_ok():
    ok, _ = gr.check_daily_counts(2, 1, 1.0, _g(max_orders_per_day=6, max_fees_per_day_usdt=3.0))
    assert ok is True


# ── 单笔 / 单币 ──

def test_notional_cap():
    assert gr.check_notional_cap(31, _g(per_order_notional_usdt=30))[0] is False
    assert gr.check_notional_cap(30, _g(per_order_notional_usdt=30))[0] is True


def test_symbol_exposure():
    bi = {"total_value": 1000.0, "positions": {"BTCUSDT.BN": {"market_value": 100.0}}}
    # 已持 100 + 新增 60 = 160 / 1000 = 16% > 15%
    assert gr.check_symbol_exposure("BTCUSDT.BN", 60, bi, 0.15)[0] is False
    assert gr.check_symbol_exposure("BTCUSDT.BN", 40, bi, 0.15)[0] is True


def test_symbol_exposure_unknown_total_passes():
    assert gr.check_symbol_exposure("X.BN", 999, {"total_value": 0}, 0.15)[0] is True


# ── 白名单 ──

def test_whitelist():
    assert gr.check_whitelist("ETHUSDT.BN", ["BTCUSDT.BN"])[0] is False
    assert gr.check_whitelist("btcusdt.bn", ["BTCUSDT.BN"])[0] is True
    assert gr.check_whitelist("ANY.BN", [])[0] is True     # 空=不限制


# ── 当日熔断 ──

def test_daily_loss_breaker_trips():
    # 资金 1000，阈值 3% = -30；亏 35 → 熔断
    assert gr.check_daily_loss(-20.0, -15.0, 1000.0, 0.03)[0] is False


def test_daily_loss_breaker_ok():
    assert gr.check_daily_loss(-10.0, -5.0, 1000.0, 0.03)[0] is True


def test_daily_loss_unknown_capital_passes():
    assert gr.check_daily_loss(-999, 0, 0, 0.03)[0] is True


# ── kill-switch（用内存库）──

def test_kill_switch_roundtrip(crypto_engine_memory_db):
    assert gr.is_killed() is False
    gr.set_killed(True)
    assert gr.is_killed() is True
    gr.set_killed(False)
    assert gr.is_killed() is False
