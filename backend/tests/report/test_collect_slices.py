"""13.2-1 行为基线：collect() 拆片。

锁两件事：
  1. 全量 façade `collect()` 的顶层键与 13.1 冻结快照逐字相同（拆片不许改变旧路径产物）
  2. 每个分片只采自己那块——尤其 `collect_positions` 不许触发最贵的买入推荐采集

所有 `_collect_*` 都被替换成哨兵，测的是**装配逻辑**，不碰 DB / 不出网。
"""
from datetime import date

import pytest

from report_engine.data_collector import ReportDataCollector, _period_bounds

pytestmark = pytest.mark.baseline

PERIOD_END = date(2026, 7, 3)

# 13.1 冻结快照的顶层键，顺序即 collect() 的装配顺序
GOLDEN_TOP_LEVEL_KEYS = [
    "report_type", "period_start", "period_end", "is_weekend",
    "market_overview", "portfolio", "previous_report", "signals",
    "backtest", "top_stocks", "trading", "news_analysis",
]

_COMMON_KEYS = ["report_type", "period_start", "period_end", "is_weekend",
                "market_overview", "portfolio"]


@pytest.fixture
def collector(monkeypatch):
    """把所有出网/碰库的私有采集方法换成哨兵，并记录调用。"""
    calls: dict = {"top_stocks_want": [], "market_overview": 0, "portfolio": 0}

    def fake_market_overview(self, session, ps, pe, enable_web_search=False):
        calls["market_overview"] += 1
        return {"__sentinel__": "market_overview"}

    def fake_portfolio(self, session):
        calls["portfolio"] += 1
        return {"positions": [{"symbol": "600519.SH"}, {"symbol": "000001.SZ"}]}

    def fake_top_stocks(self, session, ps, pe, held_symbols=None, want=("buy", "sell")):
        calls["top_stocks_want"].append(tuple(want))
        calls["top_stocks_held"] = held_symbols
        return {"buy_recommendations": [], "sell_warnings": []}

    monkeypatch.setattr(ReportDataCollector, "_collect_market_overview", fake_market_overview)
    monkeypatch.setattr(ReportDataCollector, "_collect_portfolio", fake_portfolio)
    monkeypatch.setattr(ReportDataCollector, "_collect_top_stocks", fake_top_stocks)
    monkeypatch.setattr(ReportDataCollector, "_attach_position_news", staticmethod(lambda p: None))
    monkeypatch.setattr(ReportDataCollector, "_collect_signals",
                        lambda self, s, ps, pe, held=None: {"__sentinel__": "signals"})
    monkeypatch.setattr(ReportDataCollector, "_collect_backtest",
                        lambda self, s: {"__sentinel__": "backtest"})
    monkeypatch.setattr(ReportDataCollector, "_collect_trading",
                        lambda self, s, ps, pe: {"__sentinel__": "trading"})
    monkeypatch.setattr(ReportDataCollector, "_collect_previous_recommendations",
                        lambda self, s, pe, rt: {"__sentinel__": "previous_report"})
    monkeypatch.setattr(ReportDataCollector, "_analyze_news_sentiment",
                        lambda self, d: {"__sentinel__": "news_analysis"})
    monkeypatch.setattr("report_engine.data_collector.get_session", lambda: _FakeSession())

    c = ReportDataCollector()
    c._calls = calls
    return c


class _FakeSession:
    def close(self):
        pass


# ── 1. façade 契约 ────────────────────────────────────────────────────────

def test_collect_facade_top_level_keys_unchanged(collector):
    """拆片后 collect() 的顶层键与顺序必须与 13.1 冻结快照逐字相同。"""
    data = collector.collect(report_type="weekly", period_end=PERIOD_END)
    assert list(data.keys()) == GOLDEN_TOP_LEVEL_KEYS


def test_collect_facade_matches_frozen_fixture_keys(collector, report_data_weekly):
    """双保险：直接对 13.1 那份真实快照的键集。"""
    data = collector.collect(report_type="weekly", period_end=PERIOD_END)
    assert list(data.keys()) == list(report_data_weekly.keys())


def test_collect_facade_still_asks_for_both_halves_of_top_stocks(collector):
    """全量路径必须同时要买入推荐和卖出预警（单次批量取基本面）。"""
    collector.collect(report_type="weekly", period_end=PERIOD_END)
    assert collector._calls["top_stocks_want"] == [("buy", "sell")]


def test_period_bounds_matches_fixture(report_data_weekly):
    ps, pe, is_weekend = _period_bounds("weekly", PERIOD_END)
    assert ps.isoformat() == report_data_weekly["period_start"]
    assert pe.isoformat() == report_data_weekly["period_end"]
    assert is_weekend == report_data_weekly["is_weekend"]


# ── 2. 分片只采自己那块 ────────────────────────────────────────────────────

def test_collect_market_is_common_only(collector):
    data = collector.collect_market(period_end=PERIOD_END)
    assert list(data.keys()) == _COMMON_KEYS


def test_collect_news_adds_only_news_analysis(collector):
    data = collector.collect_news(period_end=PERIOD_END)
    assert list(data.keys()) == _COMMON_KEYS + ["news_analysis"]


def test_collect_positions_never_runs_the_expensive_buy_side(collector):
    """Ch5 只用得上 sell_warnings。让它去跑买入推荐打分 = 白烧一遍最贵的采集。"""
    data = collector.collect_positions(period_end=PERIOD_END)
    assert list(data.keys()) == _COMMON_KEYS + ["signals", "top_stocks"]
    assert collector._calls["top_stocks_want"] == [("sell",)]


def test_collect_picks_only_runs_the_buy_side(collector):
    data = collector.collect_picks(period_end=PERIOD_END)
    assert list(data.keys()) == _COMMON_KEYS + ["top_stocks"]
    assert collector._calls["top_stocks_want"] == [("buy",)]


def test_collect_strategy_needs_previous_signals_backtest(collector):
    data = collector.collect_strategy(period_end=PERIOD_END)
    assert list(data.keys()) == _COMMON_KEYS + ["previous_report", "signals", "backtest"]
    assert collector._calls["top_stocks_want"] == []      # Ch6/Ch7 不碰选股


def test_slices_pass_held_symbols_from_portfolio(collector):
    collector.collect_picks(period_end=PERIOD_END)
    assert collector._calls["top_stocks_held"] == {"600519.SH", "000001.SZ"}


# ── 3. 共享片的 TTL 缓存 ───────────────────────────────────────────────────

def test_common_is_collected_once_across_slices(collector):
    """MoneyBill 连调多个章节工具时，指数/持仓只许出网采一次。"""
    collector.collect_market(period_end=PERIOD_END)
    collector.collect_news(period_end=PERIOD_END)
    collector.collect_picks(period_end=PERIOD_END)
    assert collector._calls["market_overview"] == 1
    assert collector._calls["portfolio"] == 1


def test_cache_key_separates_report_type(collector):
    """只按时间做 key 会让周报命中日报的窗口数据。"""
    collector.collect_market(report_type="weekly", period_end=PERIOD_END)
    collector.collect_market(report_type="daily", period_end=PERIOD_END)
    assert collector._calls["market_overview"] == 2


def test_cache_key_separates_period_end(collector):
    collector.collect_market(period_end=PERIOD_END)
    collector.collect_market(period_end=date(2026, 6, 26))
    assert collector._calls["market_overview"] == 2


def test_cache_returns_deep_copy_so_callers_can_mutate(collector):
    """分片会往返回值上挂自己的键；缓存里那份不能被污染。"""
    a = collector.collect_market(period_end=PERIOD_END)
    a["market_overview"]["polluted"] = True
    b = collector.collect_market(period_end=PERIOD_END)
    assert "polluted" not in b["market_overview"]


def test_cache_expires(collector, monkeypatch):
    import report_engine.data_collector as dc
    clock = {"t": 1000.0}
    monkeypatch.setattr(dc.time, "monotonic", lambda: clock["t"])
    collector.collect_market(period_end=PERIOD_END)
    clock["t"] += dc._COMMON_CACHE_TTL_SECONDS + 1
    collector.collect_market(period_end=PERIOD_END)
    assert collector._calls["market_overview"] == 2
