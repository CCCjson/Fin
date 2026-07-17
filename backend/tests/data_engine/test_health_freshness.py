"""health.get_freshness 的查库层 —— 用内存 SQLite，不碰生产库。

⚠️ 生产库事故的教训（见 memory: test-wrote-to-prod-db）：延迟 import 的写库调用会
绕过 mock。这里全程只用自建的内存 session，压根不 import get_session。

判定逻辑本身在 tests/common/test_market_freshness.py（纯规则）。本文件只守查库那半：
**market 过滤不能漏**、木桶取短板、单只票落后几天。
"""
import os
import sys
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_engine.health import (  # noqa: E402
    get_freshness,
    get_market_freshness,
    get_symbol_staleness,
)
from data_engine.storage.models import Base, DailyQuote  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed(session, market: str, day: date, n: int, start: int = 0):
    """给某市场某天灌 n 只票的 bar。"""
    for i in range(start, start + n):
        session.add(DailyQuote(symbol=f"{market}{i:05d}", market=market, date=day,
                               open=1.0, high=1.0, low=1.0, close=1.0, volume=1))
    session.commit()


def test_market_filter_is_not_dropped(session):
    """🔒 分市场统计不许漏 market 过滤 —— 「日线覆盖率 300% bug」的防复发。

    a_share 只有 10 只、hk_stock 有 100 只。漏了过滤的话 a_share 的覆盖数会被
    港股的行灌成 110，中位数基线跟着乱。
    """
    today = date(2026, 7, 17)
    for d in (date(2026, 7, 17), date(2026, 7, 16), date(2026, 7, 15)):
        _seed(session, "a_share", d, 10)
        _seed(session, "hk_stock", d, 100)

    a = get_market_freshness(session, "a_share", today)
    hk = get_market_freshness(session, "hk_stock", today)
    assert a["reference_date"] == "2026-07-17"
    assert hk["reference_date"] == "2026-07-17"
    # 两个市场各算各的：都相对自己的中位数满格，不会互相污染
    assert a["coverage_ratio_of_baseline"] == pytest.approx(1.0)
    assert hk["coverage_ratio_of_baseline"] == pytest.approx(1.0)


def test_leading_market_cannot_mask_lagging_one(session):
    """🔒 木桶取短板：A股更到今天，港股停在三天前 → latest_date 必须报港股那天。

    原实现是全表 max(date) = 取最长板 = A股的今天，港股的落后被完全盖住。
    """
    today = date(2026, 7, 17)
    for d in (date(2026, 7, 17), date(2026, 7, 16), date(2026, 7, 15)):
        _seed(session, "a_share", d, 50)
    for d in (date(2026, 7, 14), date(2026, 7, 13)):        # 港股停在 07-14
        _seed(session, "hk_stock", d, 50)

    f = get_freshness(session, today)
    assert f["latest_date"] == "2026-07-14"                  # 短板，不是 07-17
    assert f["by_market"]["a_share"]["reference_date"] == "2026-07-17"
    assert f["by_market"]["hk_stock"]["reference_date"] == "2026-07-14"
    assert f["by_market"]["hk_stock"]["is_stale"] is True    # 落后 3 个工作日
    assert f["is_stale"] is True                             # 任一市场陈旧即 True


def test_legacy_keys_survive(session):
    """老调用方（get_system_pulse / data_monitor 页）靠这几个键。"""
    _seed(session, "a_share", date(2026, 7, 17), 10)
    f = get_freshness(session, date(2026, 7, 17))
    for key in ("latest_date", "today", "is_stale", "is_weekday"):
        assert key in f


def test_empty_db_is_stale_not_crash(session):
    f = get_freshness(session, date(2026, 7, 17))
    assert f["latest_date"] is None
    assert f["is_stale"] is True


def test_symbol_staleness_measures_against_market_reference(session):
    """单只票落后几个工作日 —— 相对**市场参考交易日**，不是相对 today。"""
    today = date(2026, 7, 17)
    for d in (date(2026, 7, 17), date(2026, 7, 16), date(2026, 7, 15)):
        _seed(session, "a_share", d, 50)
    # 一只掉队的票：只有 07-15 的 bar
    session.add(DailyQuote(symbol="LAGGARD", market="a_share", date=date(2026, 7, 15),
                           open=1.0, high=1.0, low=1.0, close=1.0, volume=1))
    session.commit()

    st = get_symbol_staleness(session, "LAGGARD", "a_share", today)
    assert st["reference_date"] == "2026-07-17"
    assert st["symbol_latest"] == "2026-07-15"
    assert st["bars_behind"] == 2


def test_symbol_with_no_bars_is_unknown_not_zero(session):
    """🔒 「这只票没数据」≠「没落后」。"""
    _seed(session, "a_share", date(2026, 7, 17), 10)
    st = get_symbol_staleness(session, "NOSUCH", "a_share", date(2026, 7, 17))
    assert st["symbol_latest"] is None
    assert st["bars_behind"] is None
