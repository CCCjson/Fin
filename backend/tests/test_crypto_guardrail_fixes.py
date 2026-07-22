"""批4 护栏与时区：资金基准 / UTC 串味 / 笔数统计 / 过期 / 手续费单位 / repaint / 新鲜度。

共同主题：护栏**看起来**都在，但各自被一个不起眼的口径问题掏空了。
"""
from datetime import date, datetime, timedelta, timezone

import pytest


@pytest.fixture
def mem_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data_engine.storage.models  # noqa: F401 — 注册所有表
    from data_engine.storage import database as db
    from data_engine.storage.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    make = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(db, "get_session", lambda: make())
    return make


class TestLocalTimeDefaults:
    """⛔ server_default=func.now() 在 SQLite 上落 UTC，和同表其它本地时间列差 8 小时。"""

    def test_pending_created_at_is_local(self, mem_db):
        from data_engine.storage.models import CryptoPendingOrder
        s = mem_db()
        try:
            row = CryptoPendingOrder(order_ref="CPO-x", strategy_id="CS-1",
                                     symbol="BTCUSDT.BN", side="BUY", quantity=1.0)
            s.add(row)
            s.commit()
            s.refresh(row)
            # 与本地时钟同步（差 <60s）；旧实现在 UTC+8 环境下会差 8 小时
            assert abs((datetime.now() - row.created_at).total_seconds()) < 60
        finally:
            s.close()

    def test_created_at_aligns_with_expires_at(self, mem_db):
        """同一行内 created_at 与 expires_at 必须同口径，否则「当日」统计凭空少 8 小时。"""
        from crypto_strategy.pending import crypto_pending_service as svc
        p = svc.create_pending("CS-1", "BTCUSDT.BN", "BUY", 1.0, 100.0, quote_amount=100.0)
        created = datetime.fromisoformat(p["created_at"])
        expires = datetime.fromisoformat(p["expires_at"])
        assert timedelta(minutes=55) < expires - created < timedelta(minutes=65)

    def test_strategy_run_started_at_is_local(self, mem_db):
        from data_engine.storage.models import CryptoStrategyRun
        s = mem_db()
        try:
            row = CryptoStrategyRun(strategy_id="CS-1", status="evaluated")
            s.add(row)
            s.commit()
            s.refresh(row)
            assert abs((datetime.now() - row.started_at).total_seconds()) < 60
        finally:
            s.close()


class TestDailyOrderCount:
    """笔数护栏不能数一张会被删空的表。"""

    def test_counts_survive_rejection(self, mem_db):
        """排 3 单、拒 3 单，当日笔数仍是 3（旧实现会回落到 0，上限永远够不着）。"""
        from crypto_strategy.engine import CryptoStrategyEngine
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategyRun
        s = get_session()
        try:
            for _ in range(3):
                s.add(CryptoStrategyRun(strategy_id="CS-1", status="order_staged",
                                        orders_placed=1))
            s.commit()
        finally:
            s.close()
        # 待确认单行即使一张都不剩（全被拒绝物理删除），计数依然成立
        assert CryptoStrategyEngine()._today_counts("CS-1")["orders"] == 3

    def test_other_strategy_not_counted(self, mem_db):
        from crypto_strategy.engine import CryptoStrategyEngine
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategyRun
        s = get_session()
        try:
            s.add(CryptoStrategyRun(strategy_id="CS-OTHER", status="order_staged",
                                    orders_placed=9))
            s.commit()
        finally:
            s.close()
        assert CryptoStrategyEngine()._today_counts("CS-1")["orders"] == 0

    def test_yesterday_not_counted(self, mem_db):
        from crypto_strategy.engine import CryptoStrategyEngine
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategyRun
        s = get_session()
        try:
            s.add(CryptoStrategyRun(strategy_id="CS-1", status="order_staged", orders_placed=5,
                                    started_at=datetime.now() - timedelta(days=1)))
            s.commit()
        finally:
            s.close()
        assert CryptoStrategyEngine()._today_counts("CS-1")["orders"] == 0


class TestFeeUnits:
    """买 BTC 扣的是 BTC，不是 USDT —— 旧实现让费用护栏结构上不可能触发。"""

    @staticmethod
    def _fill(mem_db, *, fee, fee_asset, price=65000.0, qty=0.001):
        from data_engine.storage.models import CryptoFill
        s = mem_db()
        try:
            s.add(CryptoFill(source="spot", symbol="BTCUSDT.BN", trade_id="1",
                             price=price, quantity=qty, quote_qty=price * qty,
                             commission=fee, commission_asset=fee_asset, is_buyer=1,
                             trade_time=datetime.now(tz=timezone.utc).replace(tzinfo=None)))
            s.commit()
        finally:
            s.close()

    def test_base_asset_fee_converted_at_trade_price(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        self._fill(mem_db, fee=0.000001, fee_asset="BTC")
        # 0.000001 BTC × 65000 = 0.065 USDT，而不是 0.000001
        assert cb.fees_on(date.today()) == pytest.approx(0.065)

    def test_quote_asset_fee_is_face_value(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        self._fill(mem_db, fee=0.02, fee_asset="USDT")
        assert cb.fees_on(date.today()) == pytest.approx(0.02)

    def test_engine_uses_converted_fees(self, mem_db):
        from crypto_strategy.engine import CryptoStrategyEngine
        self._fill(mem_db, fee=0.000001, fee_asset="BTC")
        assert CryptoStrategyEngine()._today_counts("CS-1")["fees"] == pytest.approx(0.065)

    def test_fee_gate_can_actually_fire(self, mem_db):
        """回归本条的初衷：换算对了之后，费用上限护栏要真能拦住。"""
        from crypto_intel_engine.dsl import Guardrails
        from crypto_strategy.guardrails import check_daily_counts
        g = Guardrails(per_order_notional_usdt=100.0, max_orders_per_day=100,
                       max_fees_per_day_usdt=0.05)
        ok, reason = check_daily_counts(1, 0, 0.065, g)
        assert ok is False and "手续费" in reason


class TestExpiryOnConfirm:
    """过期只靠 cleanup 兜不住：引擎一停/后端一重启，陈单就永远可点确认。"""

    def test_expired_order_cannot_be_confirmed(self, mem_db):
        from crypto_strategy.pending import PendingError
        from crypto_strategy.pending import crypto_pending_service as svc
        from data_engine.storage.models import CryptoPendingOrder
        p = svc.create_pending("CS-1", "BTCUSDT.BN", "BUY", 1.0, 100.0, quote_amount=100.0)
        s = mem_db()
        try:
            row = s.query(CryptoPendingOrder).filter_by(order_ref=p["order_ref"]).first()
            row.expires_at = datetime.now() - timedelta(hours=72)
            s.commit()
        finally:
            s.close()
        with pytest.raises(PendingError, match="过期"):
            svc.confirm(p["order_ref"])
        with pytest.raises(PendingError):        # 过期即删，不堆积
            svc.get(p["order_ref"])

    def test_fresh_order_still_confirmable(self, mem_db, monkeypatch):
        """别把修复做成「谁都确认不了」——没过期的单必须照常走。"""
        from crypto_strategy.pending import crypto_pending_service as svc
        from tests.test_crypto_strategy_engine import _FakeBroker, _patch_confirm
        _patch_confirm(monkeypatch, broker=_FakeBroker())
        p = svc.create_pending("CS-NOPE", "BTCUSDT.BN", "BUY", 0.5, 100.0, quote_amount=50.0)
        assert svc.confirm(p["order_ref"])["status"] == "FILLED"


class TestUnclosedBarDropped:
    """7×24 没有收盘时点，未收盘 bar 进库会让信号在同一日内反复成立又消失。"""

    @staticmethod
    def _kline(open_ms, close_ms, close_px=100.0):
        return [open_ms, "100", "110", "90", str(close_px), "5",
                close_ms, "500", 10, "2.5", "250", "0"]

    def test_unclosed_bar_is_dropped(self):
        from acquisition.markets.crypto import _klines_to_df
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        day = 86_400_000
        rows = [self._kline(now_ms - 2 * day, now_ms - day - 1),      # 已收盘
                self._kline(now_ms - day, now_ms + day)]             # 还在走
        df = _klines_to_df(rows)
        assert len(df) == 1

    def test_all_unclosed_returns_empty_df(self):
        """盘中增量的常态：只剩今天那根 → 空 df，不能炸在列选择上。"""
        from acquisition.markets.crypto import _klines_to_df
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        assert _klines_to_df([self._kline(now_ms - 1000, now_ms + 86_400_000)]).empty

    def test_include_unclosed_opt_in(self):
        """日内 bar 路径刻意要保留未收盘那根（靠 REPLACE 覆盖成最终值）。"""
        from acquisition.markets.crypto import _klines_to_df
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        rows = [self._kline(now_ms - 1000, now_ms + 86_400_000)]
        assert len(_klines_to_df(rows, include_unclosed=True)) == 1


class TestBarFreshness:
    """够 60 根 ≠ 是当前的数据。"""

    @staticmethod
    def _bars(age_hours):
        base = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(hours=age_hours)
        return [{"open_time": base, "close": 100.0}]

    def test_recent_bars_are_fresh(self):
        from crypto_intel_engine.cockpit import _bars_fresh
        assert _bars_fresh(self._bars(2)) is True

    def test_stale_bars_rejected(self):
        from crypto_intel_engine.cockpit import _bars_fresh
        assert _bars_fresh(self._bars(72)) is False

    def test_empty_or_missing_timestamp_is_not_fresh(self):
        """宁可多打一次网，不可用陈数据打分。"""
        from crypto_intel_engine.cockpit import _bars_fresh
        assert _bars_fresh([]) is False
        assert _bars_fresh([{"close": 1.0}]) is False


class TestSizingCapitalBasis:
    """spec.capital_basis 被静默忽略 → live 策略每笔 BUY 恒被风控拦死。"""

    def test_sizing_uses_passed_capital_not_global(self, monkeypatch):
        from crypto_intel_engine.cockpit import size_crypto_position
        from trading_engine.risk import adapter
        monkeypatch.setattr(adapter, "get_total_capital", lambda: 1_000_000.0)  # A股口径(￥)
        r = size_crypto_position("BTCUSDT.BN", price=100.0, target_pct=10.0,
                                 broker_info={"cash": 5000.0, "total_value": 5000.0},
                                 total_capital=5000.0,          # 币安真实(USDT)
                                 max_position_pct=0.2)
        assert r["total_capital"] == pytest.approx(5000.0)
        assert r["max_single_amount"] == pytest.approx(1000.0)   # 5000×20%，不是 20 万
        assert r["amount_usdt"] == pytest.approx(500.0)          # 目标 10% = 500U

    def test_engine_passes_capital_through(self, monkeypatch):
        """守住接线本身：_size_buy 必须把 capital 透传下去。"""
        from crypto_intel_engine import cockpit
        from crypto_strategy.engine import CryptoStrategyEngine
        seen = {}

        def _spy(symbol, price, target_pct, **kw):
            seen.update(kw)
            return {"quantity": 1.0}

        monkeypatch.setattr(cockpit, "size_crypto_position", _spy)

        class _Pol:
            target_pct_source, fixed_target_pct, max_position_pct = "fixed", 0.1, 0.2

        class _Spec:
            position_policy = _Pol()

        CryptoStrategyEngine()._size_buy(_Spec(), "BTCUSDT.BN", 100.0, {}, {}, 5000.0)
        assert seen["total_capital"] == pytest.approx(5000.0)
