"""需求3 阶段3-4：半自动引擎（排队待确认，不自动成交）+ 待确认单确认闭环。

核心断言：引擎 live 只**排 CryptoPendingOrder**，绝不写 CryptoTrade；paper 只记日志；
成本/风控/去重各自拦截；确认时**重跑风控**才经 execution 成交；kill/过期正确。
"""
from datetime import datetime, timedelta

import pytest

from crypto_intel_engine.dsl import (
    Condition,
    ConditionGroup,
    CostModel,
    CryptoStrategySpec,
    EntryRules,
    ExitRules,
    Guardrails,
    PositionPolicy,
    Universe,
)
from crypto_strategy.engine import CryptoStrategyEngine
from crypto_strategy.service import _spec_to_columns


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


def _spec(mode="live", **over):
    base = dict(
        name="t", strategy_kind="swing", mode=mode,
        universe=Universe(symbols=["BTCUSDT.BN"]),
        entry_rules=EntryRules(when=ConditionGroup(
            all_of=[Condition(field="composite", op="gte", value=60)])),
        exit_rules=ExitRules(when=ConditionGroup(
            all_of=[Condition(field="composite", op="lt", value=40)])),
        # 固定滑点档：让成本闸的断言只考察纯算术，不去打币安盘口（单测必须 hermetic）。
        # 实测档（slippage_source="measured"）单独在 TestMeasuredSlippage 里用桩验证。
        cost_model=CostModel(min_net_edge_pct=0.0, slippage_source="fixed"),
        position_policy=PositionPolicy(per_symbol_exposure_cap_pct=0.99),
        guardrails=Guardrails(per_order_notional_usdt=1e9, max_orders_per_day=100),
    )
    base.update(over)
    return CryptoStrategySpec(**base)


def _seed(make, spec, enabled=1):
    from data_engine.storage.models import CryptoStrategy
    s = make()
    try:
        row = CryptoStrategy(strategy_id="CS-TEST-1", enabled=enabled, status="armed",
                             **_spec_to_columns(spec))
        s.add(row)
        s.commit()
        return row.strategy_id
    finally:
        s.close()


# 一个会触发 BUY 入场、成本充裕的分析结果
_BUY_ANALYSIS = {
    "symbol": "BTCUSDT.BN", "composite": 70.0, "recommendation": "BUY",
    "price": {"latest": 100.0}, "take_profit": 130.0,       # 毛边际 30% 远超成本
    "suggested_position_pct": 5.0, "current_position_pct": 0.0,
    "dimensions": {}, "screen": {}, "btc_regime": {}, "market_context": {},
    "derivatives_snapshot": {},
}


def _patch_common(engine, monkeypatch, analysis=None, risk_pass=True):
    import crypto_intel_engine
    monkeypatch.setattr(crypto_intel_engine, "analyze_crypto_symbol",
                        lambda sym, **kw: dict(analysis or _BUY_ANALYSIS))
    monkeypatch.setattr(engine, "_broker_and_info",
                        lambda has_key_required=False: (object(), {"total_value": 100000.0,
                                                                   "positions": {}, "cash": 100000.0,
                                                                   "unrealized_pnl": 0.0}))
    monkeypatch.setattr(engine, "_size_buy", lambda *a, **k: 0.5)
    monkeypatch.setattr(engine, "_held_qty", lambda *a, **k: 0.0)
    monkeypatch.setattr(engine, "_risk",
                        lambda *a, **k: (risk_pass, [], [] if risk_pass else ["超单币上限"]))


def _pending_count(make, status="PENDING"):
    from data_engine.storage.models import CryptoPendingOrder
    s = make()
    try:
        return s.query(CryptoPendingOrder).filter(CryptoPendingOrder.status == status).count()
    finally:
        s.close()


def _trade_count(make):
    from data_engine.storage.models import CryptoTrade
    s = make()
    try:
        return s.query(CryptoTrade).count()
    finally:
        s.close()


# ──────────────── 引擎：排单不成交 ────────────────

def test_live_stages_pending_never_trades(mem_db, monkeypatch):
    eng = CryptoStrategyEngine()
    _patch_common(eng, monkeypatch)
    _seed(mem_db, _spec(mode="live"))
    eng.run()
    assert _pending_count(mem_db) == 1        # 排了一张待确认单
    assert _trade_count(mem_db) == 0          # 绝不自动成交


def test_paper_logs_only_no_pending(mem_db, monkeypatch):
    eng = CryptoStrategyEngine()
    _patch_common(eng, monkeypatch)
    _seed(mem_db, _spec(mode="paper"))
    eng.run()
    assert _pending_count(mem_db) == 0        # paper 不排单
    assert _trade_count(mem_db) == 0
    from data_engine.storage.models import CryptoStrategyRun
    s = mem_db()
    try:
        run = s.query(CryptoStrategyRun).first()
        assert run is not None and "paper_would" in (run.decision_detail or "")
    finally:
        s.close()


def test_blocked_cost_no_pending(mem_db, monkeypatch):
    eng = CryptoStrategyEngine()
    tight = dict(_BUY_ANALYSIS, take_profit=100.2)    # 毛边际 0.2% < 往返成本
    _patch_common(eng, monkeypatch, analysis=tight)
    _seed(mem_db, _spec(mode="live"))
    eng.run()
    assert _pending_count(mem_db) == 0
    from data_engine.storage.models import CryptoStrategyRun
    s = mem_db()
    try:
        assert "blocked_cost" in (s.query(CryptoStrategyRun).first().decision_detail or "")
    finally:
        s.close()


def test_blocked_risk_no_pending(mem_db, monkeypatch):
    eng = CryptoStrategyEngine()
    _patch_common(eng, monkeypatch, risk_pass=False)
    _seed(mem_db, _spec(mode="live"))
    eng.run()
    assert _pending_count(mem_db) == 0        # 风控没过不排单


def test_dedup_same_direction(mem_db, monkeypatch):
    eng = CryptoStrategyEngine()
    _patch_common(eng, monkeypatch)
    _seed(mem_db, _spec(mode="live"))
    eng.run()
    # 第二 tick：仍命中但已有 PENDING → 不重复排。清 last_run_at 使其立即到期
    from data_engine.storage.models import CryptoStrategy
    s = mem_db()
    try:
        s.query(CryptoStrategy).update({CryptoStrategy.last_run_at: None})
        s.commit()
    finally:
        s.close()
    eng.run()
    assert _pending_count(mem_db) == 1        # 仍只有 1 张


def test_kill_switch_halts(mem_db, monkeypatch):
    from crypto_strategy import guardrails as gr
    eng = CryptoStrategyEngine()
    _patch_common(eng, monkeypatch)
    _seed(mem_db, _spec(mode="live"))
    gr.set_killed(True)
    res = eng.run()
    assert res.get("killed") is True
    assert _pending_count(mem_db) == 0        # kill 时啥也不做


# ──────────────── 待确认单：确认闭环 ────────────────

class _FakeOrder:
    def __init__(self, qty):
        from trading_engine.brokers.base import OrderStatus
        self.status = OrderStatus.FILLED
        self.filled_quantity = qty
        self.filled_price = 100.0
        self.order_id = "BTCUSDT.BN:999"
        self.commission = 0.05
        self.error_msg = None


class _FakeBroker:
    def __init__(self, cur=100.0):
        self._cur = cur
        self.last_qty = None

    def connect(self):
        return True

    def get_current_price(self, s):
        return self._cur

    def get_account_info(self):
        return {"spot_cash": 100000.0}

    def get_position(self, s):
        return None

    def submit_order(self, symbol, side, qty, price):
        self.last_qty = qty
        return _FakeOrder(qty)


def _patch_confirm(monkeypatch, risk_pass=True, broker=None):
    from acquisition.markets import binance_trade as bt
    from crypto_intel_engine import execution as ex
    from trading_engine.brokers import binance_broker
    broker = broker or _FakeBroker()
    monkeypatch.setattr(bt, "has_credentials", lambda: True)
    monkeypatch.setattr(binance_broker, "get_binance_broker", lambda: broker)
    monkeypatch.setattr(ex, "crypto_broker_info", lambda b: {"cash": 100000.0, "positions": {},
                                                             "total_value": 100000.0})
    monkeypatch.setattr(ex, "risk_check",
                        lambda *a, **k: (risk_pass, [], [] if risk_pass else ["确认时超限"]))
    return broker


def test_confirm_executes_and_records(mem_db, monkeypatch):
    from crypto_strategy.pending import crypto_pending_service as svc
    _patch_confirm(monkeypatch, risk_pass=True)      # 现价=决策价无漂移；无策略→跳条件重验
    p = svc.create_pending("CS-NOPE", "BTCUSDT.BN", "BUY", 0.5, 100.0, quote_amount=50.0)
    out = svc.confirm(p["order_ref"])
    assert out["status"] == "FILLED"
    assert _trade_count(mem_db) == 1          # 确认后才写成交台账


def test_confirm_reprices_by_current_price(mem_db, monkeypatch):
    """行情小幅上移(1%<2%阈值)：按冻结的目标 USDT 现价重算币量，不是旧币量。"""
    from crypto_strategy.pending import crypto_pending_service as svc
    broker = _patch_confirm(monkeypatch, broker=_FakeBroker(cur=101.0))
    p = svc.create_pending("CS-NOPE", "BTCUSDT.BN", "BUY", 0.5, 100.0, quote_amount=50.0)
    svc.confirm(p["order_ref"])
    assert broker.last_qty == pytest.approx(50.0 / 101.0)   # 目标$50÷现价101，非旧0.5


def test_confirm_blocks_on_big_drift(mem_db, monkeypatch):
    """行情漂移 10% 远超默认 2% → 作废删单不成交。"""
    from crypto_strategy.pending import PendingError
    from crypto_strategy.pending import crypto_pending_service as svc
    _patch_confirm(monkeypatch, broker=_FakeBroker(cur=110.0))
    p = svc.create_pending("CS-NOPE", "BTCUSDT.BN", "BUY", 0.5, 100.0, quote_amount=50.0)
    with pytest.raises(PendingError, match="漂移"):
        svc.confirm(p["order_ref"])
    assert _trade_count(mem_db) == 0
    with pytest.raises(PendingError):        # 陈单已删
        svc.get(p["order_ref"])


def test_confirm_reruns_risk_and_blocks(mem_db, monkeypatch):
    from crypto_strategy.pending import PendingError
    from crypto_strategy.pending import crypto_pending_service as svc
    _patch_confirm(monkeypatch, risk_pass=False)     # 确认时风控不过
    p = svc.create_pending("CS-NOPE", "BTCUSDT.BN", "BUY", 0.5, 100.0, quote_amount=50.0)
    with pytest.raises(PendingError):
        svc.confirm(p["order_ref"])
    assert svc.get(p["order_ref"])["status"] == "FAILED"
    assert _trade_count(mem_db) == 0          # 风控拦下，未成交


def test_reject_deletes(mem_db, monkeypatch):
    from crypto_strategy.pending import PendingError
    from crypto_strategy.pending import crypto_pending_service as svc
    p = svc.create_pending("CS-NOPE", "BTCUSDT.BN", "BUY", 0.5, 100.0, quote_amount=50.0)
    out = svc.reject(p["order_ref"])
    assert out["status"] == "REJECTED" and out["deleted"] is True
    with pytest.raises(PendingError):        # 拒绝即删
        svc.get(p["order_ref"])


def test_cleanup_deletes_expired(mem_db, monkeypatch):
    from crypto_strategy.pending import PendingError
    from crypto_strategy.pending import crypto_pending_service as svc
    p = svc.create_pending("CS-NOPE", "BTCUSDT.BN", "BUY", 0.5, 100.0, quote_amount=50.0)
    from data_engine.storage.models import CryptoPendingOrder
    s = mem_db()
    try:
        row = s.query(CryptoPendingOrder).filter_by(order_ref=p["order_ref"]).first()
        row.expires_at = datetime.now() - timedelta(minutes=1)
        s.commit()
    finally:
        s.close()
    assert svc.cleanup()["expired_deleted"] == 1
    with pytest.raises(PendingError):        # 过期即删
        svc.get(p["order_ref"])


class TestMeasuredSlippage:
    """滑点从「拍脑袋固定值」升级为「实测盘口」——用桩单簿验证，不打网。"""

    @staticmethod
    def _stub_book(monkeypatch, bids, asks):
        from acquisition.markets import crypto as crypto_mod

        class _StubFetcher:
            def __init__(self, *a, **kw):
                pass

            def get_order_book(self, symbol, limit=100):
                return {"bids": bids, "asks": asks}

        monkeypatch.setattr(crypto_mod, "CryptoFetcher", _StubFetcher)

    def test_measured_replaces_fixed_assumption(self, monkeypatch):
        """厚盘：实测滑点远小于 0.05% 假设 → 用实测值，成本判定更准。"""
        from crypto_strategy.guardrails import effective_cost_model
        self._stub_book(monkeypatch, bids=[[100.0, 1000.0]], asks=[[100.0, 1000.0]])
        cm, detail = effective_cost_model(
            CostModel(slippage_source="measured", slippage_pct=0.0005), "BTCUSDT.BN", 1000)
        assert detail["source"] == "measured"
        assert cm.slippage_pct == 0.0          # 单档就吃完，零滑点
        assert detail["fixed_assumption"] == 0.0005

    def test_thin_book_stays_conservative(self, monkeypatch):
        """薄盘被吃穿：实测值是低估的 → 取实测与固定假设的较大者，偏保守。"""
        from crypto_strategy.guardrails import effective_cost_model
        self._stub_book(monkeypatch, bids=[[100.0, 0.01]], asks=[[100.0, 0.01]])
        cm, detail = effective_cost_model(
            CostModel(slippage_source="measured", slippage_pct=0.0005), "SHIBUSDT.BN", 1_000_000)
        assert detail["thin_book"] is True
        assert cm.slippage_pct >= 0.0005       # 不因「实测=0」而低估薄盘成本

    def test_fixed_source_never_touches_network(self, monkeypatch):
        """fixed 档必须直接返回，连盘口都不查（省请求 + 保证可离线）。"""
        from crypto_strategy.guardrails import effective_cost_model

        def _boom(*a, **kw):
            raise AssertionError("fixed 档不该去取盘口")

        from acquisition.markets import crypto as crypto_mod
        monkeypatch.setattr(crypto_mod, "CryptoFetcher", _boom)
        cm, detail = effective_cost_model(
            CostModel(slippage_source="fixed", slippage_pct=0.0005), "BTCUSDT.BN", 1000)
        assert detail["source"] == "fixed" and cm.slippage_pct == 0.0005

    def test_book_failure_falls_back_to_fixed(self, monkeypatch):
        """盘口取不到 → 回落固定假设，绝不阻断决策（也不假装测过）。"""
        from acquisition.markets import crypto as crypto_mod
        from crypto_strategy.guardrails import effective_cost_model

        class _Broken:
            def __init__(self, *a, **kw):
                pass

            def get_order_book(self, symbol, limit=100):
                raise RuntimeError("网络挂了")

        monkeypatch.setattr(crypto_mod, "CryptoFetcher", _Broken)
        cm, detail = effective_cost_model(
            CostModel(slippage_source="measured", slippage_pct=0.0005), "BTCUSDT.BN", 1000)
        assert detail["source"] == "fixed" and cm.slippage_pct == 0.0005
