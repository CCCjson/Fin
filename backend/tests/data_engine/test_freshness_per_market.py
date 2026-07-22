"""新鲜度按**各市场自己的时区**判 —— 此前四个市场共用一个 UTC+8 today。

两个受害者：

- **美股**在美东。北京上午 10 点纽约还是前一晚，本地 today 比美股真实交易日超前一天。
  以前靠 `STALE_AFTER_WEEKDAYS ≥ 2` 的宽容掩盖住 —— 那是运气不是正确性。
- **crypto** 的 `daily_quotes.date` 是 UTC 日（币安 klines openTime），未收盘那根还会被
  刻意丢弃。本地 00:00–08:00 时 UTC 还在昨天，最新 bar 只可能是「前天」→ 算出落后
  2 天 → **每天凌晨误报 stale**。
"""
from datetime import date, datetime, timezone

import pytest

from common.market import A_SHARE, CRYPTO, US_STOCK

pytestmark = pytest.mark.baseline


@pytest.fixture
def mem_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data_engine.storage.models  # noqa: F401
    from data_engine.storage.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed(make, market: str, days: list[date], n: int = 50):
    from data_engine.storage.models import DailyQuote
    s = make()
    try:
        for d in days:
            for i in range(n):
                s.add(DailyQuote(symbol=f"{market}-{i}", market=market, date=d,
                                 open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
        s.commit()
    finally:
        s.close()


class TestEachMarketUsesItsOwnToday:
    def test_us_freshness_uses_new_york_today(self, mem_db, monkeypatch):
        """北京 07-22 上午 = 纽约 07-21 晚上。美股最新到 07-21 就是**最新**，不是落后。"""
        from data_engine import health
        _seed(mem_db, US_STOCK, [date(2026, 7, 20), date(2026, 7, 21)])
        s = mem_db()
        try:
            # 不传 today → 各市场各算各的
            monkeypatch.setattr(health, "market_today",
                                lambda m: date(2026, 7, 21) if m == US_STOCK
                                else date(2026, 7, 22))
            r = health.get_market_freshness(s, US_STOCK)
            assert r["reference_date"] == "2026-07-21"
            assert r["is_stale"] is False
        finally:
            s.close()

    def test_crypto_not_stale_at_local_dawn(self, mem_db, monkeypatch):
        """本地凌晨（UTC 还在昨天）不该误报 crypto stale。"""
        from data_engine import health
        # UTC 今天是 07-21；未收盘的 07-21 那根被丢弃，所以库里最新是 07-20
        _seed(mem_db, CRYPTO, [date(2026, 7, 19), date(2026, 7, 20)], n=20)
        s = mem_db()
        try:
            monkeypatch.setattr(health, "market_today", lambda m: date(2026, 7, 21))
            r = health.get_market_freshness(s, CRYPTO)
            assert r["reference_date"] == "2026-07-20"
            assert r["is_stale"] is False, "落后 1 个自然日是常态（今天那根还没收盘）"
        finally:
            s.close()

    def test_crypto_really_stale_still_reported(self, mem_db, monkeypatch):
        """别把闸门修没了：真断更还得报。"""
        from data_engine import health
        _seed(mem_db, CRYPTO, [date(2026, 7, 10), date(2026, 7, 11)], n=20)
        s = mem_db()
        try:
            monkeypatch.setattr(health, "market_today", lambda m: date(2026, 7, 21))
            assert health.get_market_freshness(s, CRYPTO)["is_stale"] is True
        finally:
            s.close()

    def test_injected_today_still_wins(self, mem_db):
        """显式传 today 仍然生效（测试注入口没被改坏）。"""
        from data_engine import health
        _seed(mem_db, A_SHARE, [date(2026, 7, 20), date(2026, 7, 21)])
        s = mem_db()
        try:
            r = health.get_market_freshness(s, A_SHARE, today=date(2026, 7, 21))
            assert r["reference_date"] == "2026-07-21"
        finally:
            s.close()


class TestMarketTodayActuallyDiffers:
    def test_us_and_shanghai_disagree_in_the_morning(self):
        """不 mock，直接验真源：北京上午时两个市场的「今天」确实不是同一天。"""
        from common.market_time import market_now, market_today
        sh_hour = market_now(A_SHARE).hour
        if not (0 <= sh_hour < 12):
            pytest.skip("只有北京上午这段两者才必然不同")
        assert market_today(US_STOCK) < market_today(A_SHARE)

    def test_crypto_today_is_utc_today(self):
        from common.market_time import market_today
        assert market_today(CRYPTO) == datetime.now(tz=timezone.utc).date()
