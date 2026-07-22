"""批2 成本价重建：加权回放 / 手续费折算 / 覆盖度三态 / 日亏阈值单位。

核心不变量：**算不出成本时返回 0 而不是现价**。返回现价会让 StopLossRule 算出
「亏损 0%」永远放行；返回 0 则会走「无持仓成本，跳过」明说跳过。
"""
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def mem_db(monkeypatch):
    """内存库 + 建表，并把 get_session 换掉（含延迟 import 的调用点）。"""
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


def _fill(mem_db, *, tid, symbol="BTCUSDT.BN", price, qty, is_buyer, fee=0.0,
          fee_asset="USDT", when=None, quote=None):
    from data_engine.storage.models import CryptoFill
    s = mem_db()
    try:
        s.add(CryptoFill(
            source="spot", symbol=symbol, trade_id=str(tid), order_id=str(tid),
            price=price, quantity=qty, quote_qty=quote if quote is not None else price * qty,
            commission=fee, commission_asset=fee_asset, is_buyer=1 if is_buyer else 0,
            trade_time=when or datetime(2026, 7, 20, 12, 0, 0)))
        s.commit()
    finally:
        s.close()


class TestWeightedReplay:
    def test_two_buys_weighted_average(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True)
        _fill(mem_db, tid=2, price=200.0, qty=1.0, is_buyer=True)
        st = cb.replay()["BTCUSDT.BN"]
        assert st["quantity"] == pytest.approx(2.0)
        assert st["avg_cost"] == pytest.approx(150.0)

    def test_sell_realizes_pnl_and_keeps_avg_cost(self, mem_db):
        """卖出只结转盈亏，不改变剩余持仓的加权成本。"""
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=2.0, is_buyer=True)
        _fill(mem_db, tid=2, price=150.0, qty=1.0, is_buyer=False)
        st = cb.replay()["BTCUSDT.BN"]
        assert st["quantity"] == pytest.approx(1.0)
        assert st["avg_cost"] == pytest.approx(100.0)     # 成本不因卖出而变
        assert st["closed"][0]["pnl"] == pytest.approx(50.0)

    def test_ordering_is_by_time_not_insert_order(self, mem_db):
        """乱序插入也要按成交时间回放，否则成本算错。"""
        from crypto_intel_engine import cost_basis as cb
        base = datetime(2026, 7, 20, 12, 0, 0)
        _fill(mem_db, tid=2, price=200.0, qty=1.0, is_buyer=True, when=base + timedelta(hours=1))
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True, when=base)
        assert cb.replay()["BTCUSDT.BN"]["avg_cost"] == pytest.approx(150.0)


class TestCommissionAsset:
    """⛔ commissionAsset 单位不可混加：买 BTC 扣的是 BTC，不是 USDT。"""

    def test_base_asset_fee_reduces_received_quantity(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        # 花 100 USDT 买 1 BTC，手续费 0.001 BTC → 实际到手 0.999 BTC
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True, fee=0.001, fee_asset="BTC")
        st = cb.replay()["BTCUSDT.BN"]
        assert st["quantity"] == pytest.approx(0.999)
        assert st["avg_cost"] == pytest.approx(100.0 / 0.999)   # 单位成本被抬高

    def test_quote_asset_fee_increases_cost(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True, fee=0.1, fee_asset="USDT")
        st = cb.replay()["BTCUSDT.BN"]
        assert st["quantity"] == pytest.approx(1.0)
        assert st["avg_cost"] == pytest.approx(100.1)

    def test_unpriceable_fee_reported_not_silently_zero(self, mem_db):
        """BNB 抵扣手续费但本地没有 BNB 日线 → 如实报出来，不当 0 也不瞎猜。"""
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True, fee=0.05, fee_asset="BNB")
        st = cb.replay()["BTCUSDT.BN"]
        assert st["unpriced_fees"].get("BNB") == pytest.approx(0.05)
        assert st["avg_cost"] == pytest.approx(100.0)     # 没瞎加一个假数进成本

    def test_bnb_fee_priced_from_local_daily_bar(self, mem_db):
        """本地有 BNBUSDT 日线时，手续费按当日收盘折算进成本。"""
        from crypto_intel_engine import cost_basis as cb
        from data_engine.storage.models import DailyQuote
        s = mem_db()
        try:
            s.add(DailyQuote(symbol="BNBUSDT.BN", market="crypto", date=date(2026, 7, 20),
                             open=600.0, high=600.0, low=600.0, close=600.0, volume=1.0))
            s.commit()
        finally:
            s.close()
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True, fee=0.05, fee_asset="BNB")
        st = cb.replay()["BTCUSDT.BN"]
        assert st["avg_cost"] == pytest.approx(100.0 + 0.05 * 600.0)
        assert st["unpriced_fees"] == {}


class TestCoverage:
    """覆盖度三态 —— 项目 P0-2「数据不可信就明说」哲学在成本层的落地。"""

    def test_no_fills_is_unknown_with_zero_cost(self, mem_db):
        """⭐ 关键不变量：算不出成本 → avg_cost=0（让止损规则明说跳过），绝不返回现价。"""
        from crypto_intel_engine import cost_basis as cb
        r = cb.cost_for("BTCUSDT.BN", actual_quantity=1.0)
        assert r["quality"] == "unknown"
        assert r["avg_cost"] == 0.0

    def test_full_coverage_when_replay_matches_balance(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True)
        r = cb.cost_for("BTCUSDT.BN", actual_quantity=1.0)
        assert r["quality"] == "full" and r["avg_cost"] == pytest.approx(100.0)

    def test_partial_when_holding_more_than_replayed(self, mem_db):
        """买了 1 个但账上有 2 个 → 多的那个是充值/空投，成本只覆盖一半，要明说。"""
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True)
        r = cb.cost_for("BTCUSDT.BN", actual_quantity=2.0)
        assert r["quality"] == "partial"
        assert r["coverage"] == pytest.approx(0.5)
        assert r["avg_cost"] == pytest.approx(100.0)      # 已知那部分的成本仍然可信
        assert "来源不明" in r["note"]

    def test_withdrawal_still_counts_as_full(self, mem_db):
        """回放比余额多（提币出去了）不影响成本可信度。"""
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=2.0, is_buyer=True)
        assert cb.cost_for("BTCUSDT.BN", actual_quantity=1.0)["quality"] == "full"


class TestUncostedSell:
    """系统外买入的币被卖掉：绝不能按 0 成本算成暴利（会灌爆当日已实现盈亏、废掉熔断）。"""

    def test_sell_without_buy_record_is_not_pure_profit(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=150.0, qty=1.0, is_buyer=False)   # 只有卖，没有买
        st = cb.replay()["BTCUSDT.BN"]
        assert st["has_uncosted_sell"] is True
        assert st["closed"] == []                 # 没有成本就不编造一笔盈亏
        assert st["quantity"] == 0.0              # 也不允许持仓量变负

    def test_partial_costed_sell_only_counts_known_part(self, mem_db):
        """买过 1 个、卖掉 2 个：只对有成本的那 1 个结转盈亏。"""
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=1.0, is_buyer=True)
        _fill(mem_db, tid=2, price=150.0, qty=2.0, is_buyer=False,
              when=datetime(2026, 7, 20, 13, 0, 0))
        st = cb.replay()["BTCUSDT.BN"]
        assert st["has_uncosted_sell"] is True
        # 卖出所得按比例只取有成本的那半：150 - 100 = 50
        assert st["closed"][0]["pnl"] == pytest.approx(50.0)


class TestClosedPnlAndRealized:
    def test_closed_pnls_newest_first(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=2.0, is_buyer=True,
              when=datetime(2026, 7, 18, 10, 0))
        _fill(mem_db, tid=2, price=90.0, qty=1.0, is_buyer=False,
              when=datetime(2026, 7, 19, 10, 0))     # 亏 10
        _fill(mem_db, tid=3, price=130.0, qty=1.0, is_buyer=False,
              when=datetime(2026, 7, 20, 10, 0))     # 赚 30
        pnls, last_loss = cb.closed_pnls(limit=10)
        assert pnls[0] == pytest.approx(30.0)        # 最新在前
        assert last_loss == "2026-07-19"

    def test_realized_on_specific_day(self, mem_db):
        from crypto_intel_engine import cost_basis as cb
        _fill(mem_db, tid=1, price=100.0, qty=2.0, is_buyer=True,
              when=datetime(2026, 7, 18, 10, 0))
        _fill(mem_db, tid=2, price=130.0, qty=1.0, is_buyer=False,
              when=datetime(2026, 7, 20, 10, 0))
        assert cb.realized_on(date(2026, 7, 20)) == pytest.approx(30.0)
        assert cb.realized_on(date(2026, 7, 19)) == 0.0


class TestSyncIdempotency:
    def test_reinsert_same_trade_id_is_noop(self, mem_db, monkeypatch):
        """全量回填可以反复跑：撞 (source,symbol,trade_id) 唯一键即跳过。"""
        from acquisition.markets import binance_trade as bt
        from crypto_intel_engine import cost_basis as cb
        batch = [{"id": 1, "orderId": 9, "price": "100", "qty": "1", "quoteQty": "100",
                  "commission": "0", "commissionAsset": "USDT", "isBuyer": True,
                  "time": 1784000000000}]
        monkeypatch.setattr(bt, "has_credentials", lambda: True)
        monkeypatch.setattr(bt, "my_trades", lambda s, from_id=None, limit=1000:
                            batch if from_id in (0, None) else [])
        assert cb.sync_symbol_fills("BTCUSDT.BN", full=True)["inserted"] == 1
        assert cb.sync_symbol_fills("BTCUSDT.BN", full=True)["inserted"] == 0   # 幂等
        assert cb.replay()["BTCUSDT.BN"]["fills"] == 1

    def test_pagination_follows_from_id(self, mem_db, monkeypatch):
        """翻页必须用 fromId=上页末条+1（时间窗最多 24h，拉不了全历史）。"""
        from acquisition.markets import binance_trade as bt
        from crypto_intel_engine import cost_basis as cb
        pages = {0: [{"id": i, "orderId": i, "price": "100", "qty": "0.001",
                      "quoteQty": "0.1", "commission": "0", "commissionAsset": "USDT",
                      "isBuyer": True, "time": 1784000000000 + i}
                     for i in range(1000)],
                 1000: [{"id": 1000, "orderId": 1000, "price": "100", "qty": "0.001",
                         "quoteQty": "0.1", "commission": "0", "commissionAsset": "USDT",
                         "isBuyer": True, "time": 1784000001000}]}
        seen = []

        def _my_trades(s, from_id=None, limit=1000):
            seen.append(from_id)
            return pages.get(from_id, [])

        monkeypatch.setattr(bt, "has_credentials", lambda: True)
        monkeypatch.setattr(bt, "my_trades", _my_trades)
        r = cb.sync_symbol_fills("BTCUSDT.BN", full=True)
        assert seen == [0, 1000]           # 第二页从末条 id+1 起
        assert r["inserted"] == 1001

    def test_network_failure_keeps_partial_progress(self, mem_db, monkeypatch):
        """中途失败不抛：已拉到的留下，下次续拉（同步失败只该让覆盖度降级）。"""
        from acquisition.markets import binance_trade as bt
        from crypto_intel_engine import cost_basis as cb

        def _boom(s, from_id=None, limit=1000):
            raise TimeoutError("read timeout")

        monkeypatch.setattr(bt, "has_credentials", lambda: True)
        monkeypatch.setattr(bt, "my_trades", _boom)
        r = cb.sync_symbol_fills("BTCUSDT.BN", full=True)
        assert r["error"] and r["inserted"] == 0


class TestCryptoRiskConfig:
    """日亏阈值单位串味：A股人民币总资金 × 3% 不能拿去比币安 USDT 浮亏。"""

    def test_threshold_uses_real_binance_total_value(self, monkeypatch):
        from crypto_intel_engine.execution import crypto_risk_config
        from trading_engine.risk import adapter
        monkeypatch.setattr(adapter, "get_total_capital", lambda: 5000.0)   # A股口径(￥)
        cfg = crypto_risk_config({"total_value": 100000.0})                 # 币安真实(USDT)
        assert cfg["max_daily_loss"] == pytest.approx(3000.0)   # 100000 × 3%，不是 5000 × 3%
        assert "max_daily_loss_pct" not in cfg                  # 别让 RiskManager 回落到全局资金

    def test_falls_back_to_pct_when_total_unknown(self, monkeypatch):
        """读不到真实总值时保留百分比走旧路径——总比完全没有日亏闸好。"""
        from crypto_intel_engine.execution import crypto_risk_config
        cfg = crypto_risk_config({})
        assert "max_daily_loss_pct" in cfg and "max_daily_loss" not in cfg


class TestStopLossReactivated:
    """回归本批的初衷：成本价回来后，止损规则必须真的能触发。"""

    def test_stop_loss_fires_with_real_cost(self):
        from trading_engine.risk.rules import StopLossRule
        r = StopLossRule(0.05).check(symbol="BTCUSDT.BN", avg_cost=100.0, current_price=90.0)
        assert r.passed is False        # 亏 10% > 5% 止损线

    def test_stop_loss_skips_loudly_when_cost_unknown(self):
        """成本未知时明说跳过，而不是拿现价算出「亏损 0%」假装安全。"""
        from trading_engine.risk.rules import StopLossRule
        r = StopLossRule(0.05).check(symbol="BTCUSDT.BN", avg_cost=0.0, current_price=90.0)
        assert r.passed is True and "无持仓成本" in r.message

    def test_old_behaviour_would_never_fire(self):
        """反证：旧实现 avg_cost=现价 → loss_pct 恒 0 → 永不触发（这就是被修掉的病）。"""
        from trading_engine.risk.rules import StopLossRule
        r = StopLossRule(0.05).check(symbol="BTCUSDT.BN", avg_cost=90.0, current_price=90.0)
        assert r.passed is True
