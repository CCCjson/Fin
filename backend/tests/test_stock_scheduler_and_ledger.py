"""股票策略的调度与成交台账（S5 欠债补齐）。

两件事最要紧：
1. **闭市不 tick** —— 否则拿的是上一交易日的收盘数据，命中了也只会排出开盘就作废的单。
2. **归因当场写** —— S1 踩过：靠桥表事后 join，那张表 24 小时后清理，
   「这笔属于哪条策略」永久查不回来。
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from data_engine.storage.database import get_session
from data_engine.storage.models import StrategyTrade
from strategy_runtime.ledger import record_fill, strategy_fills
from strategy_runtime.scheduler import StockStrategyScheduler


@pytest.fixture
def clean():
    yield
    s = get_session()
    try:
        s.query(StrategyTrade).delete()
        s.commit()
    finally:
        s.close()


# ── 成交台账 ────────────────────────────────────────────────────────────

class TestLedger:
    def test_attribution_is_written_with_the_fill(self, clean):
        """🔴 归因写在成交行本身，不是事后 join。"""
        rid = record_fill(market="a_share", symbol="600519.SH", side="BUY",
                          price=1700.0, quantity=100, strategy_id="CS-1",
                          rule_set="趋势", mode="paper", commission=5.0)
        assert rid is not None
        rows = strategy_fills("CS-1")
        assert len(rows) == 1
        r = rows[0]
        assert (r["symbol"], r["side"], r["quantity"]) == ("600519.SH", "BUY", 100)
        assert r["rule_set"] == "趋势"
        assert r["amount"] == pytest.approx(170000.0)

    def test_paper_and_live_are_separate_buckets(self, clean):
        """🔒 paper 是理想撮合 —— 跟真钱成绩加在一起等于拿模拟成绩给真钱决策背书。"""
        record_fill(market="a_share", symbol="600519.SH", side="BUY", price=10,
                    quantity=100, strategy_id="CS-1", rule_set=None, mode="paper")
        record_fill(market="a_share", symbol="600519.SH", side="BUY", price=10,
                    quantity=200, strategy_id="CS-1", rule_set=None, mode="live")
        assert len(strategy_fills("CS-1", mode="paper")) == 1
        assert len(strategy_fills("CS-1", mode="live")) == 1
        # 不传 mode 时两种都回 —— 调用方必须自己分桶
        assert len(strategy_fills("CS-1")) == 2

    def test_write_failure_does_not_raise(self, clean):
        """⚠️ 钱已经动了 —— 写台账失败往上抛只会让上层以为没成交而**重复下单**。"""
        with patch("data_engine.storage.database.get_session",
                   side_effect=RuntimeError("库炸了")):
            assert record_fill(market="a_share", symbol="600519.SH", side="BUY",
                               price=10, quantity=100, strategy_id="CS-1",
                               rule_set=None, mode="paper") is None

    def test_trade_date_uses_the_market_local_day(self, clean):
        """⚠️ 美股的 8/2 不是北京时间的 8/2。"""
        record_fill(market="us_stock", symbol="AAPL", side="BUY", price=200,
                    quantity=10, strategy_id="CS-US", rule_set=None, mode="paper")
        from common.market_time import market_today
        assert strategy_fills("CS-US")[0]["trade_date"] == \
            market_today("us_stock").isoformat()


# ── 调度 ────────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, sid="CS-1", market="a_share", mode="paper",
                 interval=30, last_run_at=None):
        self.strategy_id = sid
        self.market = market
        self.mode = mode
        self.interval_minutes = interval
        self.last_run_at = last_run_at


class TestScheduler:
    def test_closed_market_is_not_ticked(self):
        """⭐ 闭市不跑 —— 拿到的是上一交易日的收盘数据，命中了也只会排出
        开盘就作废的单，还白打一次分析卡链路。"""
        sch = StockStrategyScheduler()
        with patch("strategy_runtime.scheduler.is_open", return_value=False), \
             patch("crypto_strategy.service.spec_from_row") as spec_from_row:
            spec_from_row.return_value = type("S", (), {"market": "a_share",
                                                        "interval_minutes": 30})()
            out = sch.tick_one(_Row())
        assert "未开市" in out["skipped"]

    def test_not_due_is_skipped(self):
        from common.market_time import utc_now
        sch = StockStrategyScheduler()
        row = _Row(last_run_at=utc_now() - timedelta(minutes=5))   # 间隔 30 分钟
        with patch("strategy_runtime.scheduler.is_open", return_value=True), \
             patch("crypto_strategy.service.spec_from_row") as spec_from_row:
            spec_from_row.return_value = type("S", (), {"market": "a_share",
                                                        "interval_minutes": 30})()
            out = sch.tick_one(row)
        assert "未到 tick 间隔" in out["skipped"]

    def test_settle_runs_once_per_local_day(self):
        """🔒 T+1 解冻一天只能一次 —— 每次 tick 都调等于取消了 T+1。"""
        sch = StockStrategyScheduler()
        calls = []

        class _Ad:
            def settle_new_day(self):
                calls.append(1)

        with patch("strategy_runtime.scheduler.market_today",
                   return_value=date(2026, 8, 3)):
            sch._settle_if_new_day("a_share", _Ad())
            sch._settle_if_new_day("a_share", _Ad())
            sch._settle_if_new_day("a_share", _Ad())
        assert calls == [1], "同一天解冻了多次"

        with patch("strategy_runtime.scheduler.market_today",
                   return_value=date(2026, 8, 4)):
            sch._settle_if_new_day("a_share", _Ad())
        assert len(calls) == 2, "跨日应该再解冻一次"

    def test_each_market_settles_on_its_own_day(self):
        """A 股和美股的「新的一天」不是同一时刻。"""
        sch = StockStrategyScheduler()
        calls = []

        class _Ad:
            def settle_new_day(self):
                calls.append(1)

        with patch("strategy_runtime.scheduler.market_today",
                   return_value=date(2026, 8, 3)):
            sch._settle_if_new_day("a_share", _Ad())
            sch._settle_if_new_day("us_stock", _Ad())
        assert len(calls) == 2, "两个市场应该各自解冻一次"

    def test_one_bad_strategy_does_not_stop_the_sweep(self):
        """⚠️ 一条坏策略不该让别的策略整天不交易。"""
        sch = StockStrategyScheduler()
        rows = [_Row("CS-bad"), _Row("CS-ok")]

        def _tick(row):
            if row.strategy_id == "CS-bad":
                raise RuntimeError("炸了")
            return {"strategy_id": row.strategy_id, "ok": True}

        with patch.object(sch, "_enabled_stock_strategies", return_value=rows), \
             patch.object(sch, "tick_one", side_effect=_tick):
            out = sch.sweep()
        assert out["checked"] == 2
        assert any("异常" in str(r.get("skipped", "")) for r in out["results"])

    def test_only_stock_strategies_are_swept(self):
        """⚠️ crypto 策略有它自己的引擎 —— 两边都跑会让同一条策略被下两次单。"""
        import inspect
        src = inspect.getsource(StockStrategyScheduler._enabled_stock_strategies)
        assert 'CryptoStrategy.market.in_(("a_share", "us_stock"))' in src
