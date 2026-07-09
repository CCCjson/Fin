"""
DailyUpdater.get_update_status() 覆盖率回归测试。

背景：Jason 截图发现「日线K线」卡片显示 301.3%（19,871 / 6,594 只到最新）。
根因实测坐实：total_stocks 只统计 market=="a_share"，但 fresh_count 的两个查询
（latest_date 与 distinct symbol 计数）都漏了同样的市场过滤，把 A股+港股+美股
三个市场的股票数加在一起当分子——数字上恰好是 a_share 6,567 + hk_stock 2,668 +
us_stock 10,636 = 19,871。

用一个隔离的内存 SQLite 库构造「三市场同日都有数据」的场景，直接复现并锁死修复：
覆盖率永远不能超过 100%，且只统计 A股。
"""

import pytest

pytestmark = pytest.mark.network
import os
import sys
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import StockInfo, DailyQuote  # noqa: E402
from data_engine import daily_updater as du_mod  # noqa: E402


@pytest.fixture()
def isolated_db(monkeypatch):
    """内存 SQLite，跟真实 data/market.db 完全隔离；只 monkeypatch get_session。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(du_mod, "get_session", lambda: Session())
    return Session


def _seed_stock(session, symbol: str, market: str):
    session.add(StockInfo(
        symbol=symbol, name=symbol, market=market,
        stock_type="stock", is_active=1,
    ))


def _seed_quote(session, symbol: str, market: str, d: date):
    session.add(DailyQuote(
        symbol=symbol, market=market, date=d,
        open=1.0, high=1.0, low=1.0, close=1.0, volume=100.0,
    ))


class TestCoverageCrossMarketBug:

    def test_coverage_never_exceeds_100_with_cross_market_pollution(self, isolated_db):
        """复现截图场景的缩小版：A股 2 只、港股 1 只、美股 1 只，同一天都有行情。
        修复前：fresh_count 数 4（跨市场），total_stocks 数 2（仅 A股）→ 200%。
        修复后：fresh_count 只数 A股的 2 只 → 100%。
        """
        session = isolated_db()
        today = date(2026, 7, 6)
        _seed_stock(session, "600519.SH", "a_share")
        _seed_stock(session, "000001.SZ", "a_share")
        _seed_quote(session, "600519.SH", "a_share", today)
        _seed_quote(session, "000001.SZ", "a_share", today)
        # 港美股：同一天也有行情，但不该被算进 A股 覆盖率的分子
        _seed_quote(session, "0700.HK", "hk_stock", today)
        _seed_quote(session, "AAPL", "us_stock", today)
        session.commit()
        session.close()

        result = du_mod.DailyUpdater().get_update_status()

        assert result["total_stocks"] == 2
        assert result["stocks_at_latest"] == 2  # 不能是 4
        assert result["coverage_pct"] == 100.0  # 不能是 200.0
        assert result["latest_date"] == "2026-07-06"

    def test_latest_date_not_skewed_by_other_markets(self, isolated_db):
        """港美股比 A股 更新得更晚（如 A股 节假日、美股正常交易）时，
        latest_date 不该被带偏成美股的日期——这张卡片是 A股 专属口径。
        """
        session = isolated_db()
        a_share_date = date(2026, 7, 3)   # A股 最后交易日（周五）
        us_date = date(2026, 7, 6)        # 美股更晚一天有数据
        _seed_stock(session, "600519.SH", "a_share")
        _seed_quote(session, "600519.SH", "a_share", a_share_date)
        _seed_quote(session, "AAPL", "us_stock", us_date)
        session.commit()
        session.close()

        result = du_mod.DailyUpdater().get_update_status()

        assert result["latest_date"] == "2026-07-03"  # 不能是 us_date
        assert result["stocks_at_latest"] == 1
        assert result["coverage_pct"] == 100.0

    def test_coverage_zero_when_no_a_share_data(self, isolated_db):
        """只有港美股数据、A股 一条都没有：分子分母都该反映「A股 没数据」，
        而不是被港美股的存在误报成有覆盖。
        """
        session = isolated_db()
        _seed_stock(session, "600519.SH", "a_share")
        _seed_quote(session, "AAPL", "us_stock", date(2026, 7, 6))
        session.commit()
        session.close()

        result = du_mod.DailyUpdater().get_update_status()

        assert result["latest_date"] is None
        assert result["stocks_at_latest"] == 0
        assert result["coverage_pct"] == 0

    def test_no_stocks_no_division_by_zero(self, isolated_db):
        result = du_mod.DailyUpdater().get_update_status()
        assert result["total_stocks"] == 0
        assert result["coverage_pct"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
