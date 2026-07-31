"""缺口引擎 —— 「中间的洞」能被扫出来、能被补上、假期不会被误报。

这套测试守的是三条最容易回退的性质：

1. **中间的洞逮得住**。现有 updater 全是前沿式的（从 `max(date)+1` 往后拉），
   对 07-10~07-15 空、07-16 有数据这种形态结构性失明。
2. **假期不误报**。日历里没有的日子不算缺口 —— 实测现场美股 2026-07-03 是独立日
   休市（7/4 落在周六，周五补休），库里那 5 行 OTC 杂数据不该被当成「缺了 11000 只」。
3. **`suspected` 永不自动补**。没有日历时退回工作日启发式，那种缺口只许展示 ——
   港美股误补一次要白打 10-15 分钟 Yahoo。
"""
from datetime import date, timedelta

import pytest

from common.market import A_SHARE, US_STOCK

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


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _seed_quotes(session, market: str, days: list[date], n: int = 100):
    from data_engine.storage.models import DailyQuote
    for d in days:
        for i in range(n):
            session.add(DailyQuote(symbol=f"{market}-{i}", market=market, date=d,
                                   open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
    session.commit()


def _seed_calendar(session, market: str, days: list[date]):
    from data_engine.storage.models import TradingCalendar
    for d in days:
        session.add(TradingCalendar(market=market, cal_date=d, source="test"))
    session.commit()


class TestScanFindsMiddleHoles:
    def test_middle_hole_is_detected(self, mem_db):
        """07-10~07-15 全空、07-16 之后正常 —— 前沿式 updater 永远看不见，这里必须逮到。"""
        from data_engine.gap_engine import scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 5, 1), date(2026, 7, 24))
        hole = [d for d in window if date(2026, 7, 10) <= d <= date(2026, 7, 15)]
        have = [d for d in window if d not in hole]

        s = mem_db()
        try:
            _seed_calendar(s, A_SHARE, window)
            _seed_quotes(s, A_SHARE, have)
            r = scan_asset(s, "daily.a_share", today=today, lookback_days=120)
        finally:
            s.close()

        assert r["confidence"] == "certain"
        assert {g["date"] for g in r["gaps"]} == {d.isoformat() for d in hole}

    def test_partial_day_counts_as_gap(self, mem_db):
        """「跑了但只落了 5 行」也是缺口 —— 只看「有没有行」会把它判成健康。"""
        from data_engine.gap_engine import scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), date(2026, 7, 24))
        broken = date(2026, 7, 8)
        s = mem_db()
        try:
            _seed_calendar(s, A_SHARE, window)
            _seed_quotes(s, A_SHARE, [d for d in window if d != broken], n=100)
            _seed_quotes(s, A_SHARE, [broken], n=3)      # 残缺日：只有 3 只
            r = scan_asset(s, "daily.a_share", today=today, lookback_days=120)
        finally:
            s.close()

        assert [g["date"] for g in r["gaps"]] == [broken.isoformat()]
        assert r["gaps"][0]["observed"] == 3

    def test_holiday_not_reported(self, mem_db):
        """日历里没有的日子不是缺口 —— 美股 2026-07-03 独立日休市那一档。"""
        from data_engine.gap_engine import scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), date(2026, 7, 24))
        holiday = date(2026, 7, 3)
        trading = [d for d in window if d != holiday]   # 日历里就没有 07-03

        s = mem_db()
        try:
            _seed_calendar(s, US_STOCK, trading)
            _seed_quotes(s, US_STOCK, trading, n=100)
            _seed_quotes(s, US_STOCK, [holiday], n=5)   # 库里有 5 行 OTC 杂数据
            r = scan_asset(s, "daily.us_stock", today=today, lookback_days=120)
        finally:
            s.close()

        assert r["gaps"] == [], "假期被误报成缺口了 —— 日历自证这条防线破了"

    def test_days_before_asset_started_are_not_gaps(self, mem_db):
        """资产开始积累之前的日子不算缺口（涨停池只有 10 天数据那一档）。"""
        from data_engine.gap_engine import scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 5, 1), date(2026, 7, 24))
        started = date(2026, 7, 6)
        s = mem_db()
        try:
            _seed_calendar(s, A_SHARE, window)
            _seed_quotes(s, A_SHARE, [d for d in window if d >= started])
            r = scan_asset(s, "daily.a_share", today=today, lookback_days=120)
        finally:
            s.close()

        assert r["gaps"] == []
        assert r["since"] == started.isoformat()

    def test_tail_buffer_not_reported(self, mem_db):
        """最近两天不判缺口 —— 「今天还没跑」归尾部补跑管，不是缺口。"""
        from data_engine.gap_engine import scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), today)
        # 库里停在 07-23，07-24/27 还没跑
        have = [d for d in window if d <= date(2026, 7, 23)]
        s = mem_db()
        try:
            _seed_calendar(s, A_SHARE, window)
            _seed_quotes(s, A_SHARE, have)
            r = scan_asset(s, "daily.a_share", today=today, lookback_days=120)
        finally:
            s.close()

        assert r["gaps"] == []


class TestSuspectedNeverAutoFills:
    def test_no_calendar_degrades_to_suspected(self, mem_db):
        """日历没建起来 → 退回工作日启发式，且必须标 suspected。"""
        from data_engine.gap_engine import scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), date(2026, 7, 24))
        s = mem_db()
        try:
            # 刻意不塞日历
            _seed_quotes(s, A_SHARE, [d for d in window if d != date(2026, 7, 8)])
            r = scan_asset(s, "daily.a_share", today=today, lookback_days=120)
        finally:
            s.close()

        assert r["confidence"] == "suspected"

    def test_suspected_gaps_are_never_fillable(self, mem_db):
        """`fillable_gaps` 只收 certain —— suspected 可能是假期，自动补要白烧代理/Yahoo。"""
        from data_engine.gap_engine import fillable_gaps, scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), date(2026, 7, 24))
        s = mem_db()
        try:
            _seed_quotes(s, A_SHARE, [d for d in window if d != date(2026, 7, 8)])
            scan_asset(s, "daily.a_share", today=today, lookback_days=120)
            todo = fillable_gaps(s)
        finally:
            s.close()

        assert todo == {}, "suspected 缺口混进了自动补齐队列"

    def test_calendar_arriving_upgrades_suspected_to_open(self, mem_db):
        """日历后来建起来了，原本 suspected 的缺口要升级成 open 才可能被补。"""
        from data_engine.gap_engine import fillable_gaps, scan_asset

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), date(2026, 7, 24))
        hole = date(2026, 7, 8)
        s = mem_db()
        try:
            _seed_quotes(s, A_SHARE, [d for d in window if d != hole])
            scan_asset(s, "daily.a_share", today=today, lookback_days=120)
            assert fillable_gaps(s) == {}

            _seed_calendar(s, A_SHARE, window)          # 日历到位
            r = scan_asset(s, "daily.a_share", today=today, lookback_days=120)
            assert r["confidence"] == "certain"
            todo = fillable_gaps(s)
        finally:
            s.close()

        assert todo == {"daily.a_share": [hole]}


class TestGapLifecycle:
    def test_permanent_is_never_reopened(self, mem_db):
        """反证出来的假期不许被重新打开 —— 否则每次启动都要重试同一个补不上的洞。"""
        from data_engine.gap_engine import fillable_gaps, scan_asset
        from data_engine.storage.models import DataGap

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), date(2026, 7, 24))
        hole = date(2026, 7, 8)
        s = mem_db()
        try:
            _seed_calendar(s, A_SHARE, window)
            _seed_quotes(s, A_SHARE, [d for d in window if d != hole])
            scan_asset(s, "daily.a_share", today=today, lookback_days=120)

            row = s.query(DataGap).filter(DataGap.gap_date == hole).one()
            row.status = "permanent"
            row.attempts = 3
            s.commit()

            scan_asset(s, "daily.a_share", today=today, lookback_days=120)
            row = s.query(DataGap).filter(DataGap.gap_date == hole).one()
            todo = fillable_gaps(s)
        finally:
            s.close()

        assert row.status == "permanent"
        assert todo == {}

    def test_filled_gap_is_closed_on_rescan(self, mem_db):
        """补上之后重扫要自动收口，否则前端红区永远挂着。"""
        from data_engine.gap_engine import scan_asset
        from data_engine.storage.models import DataGap

        today = date(2026, 7, 27)
        window = _weekdays(date(2026, 6, 1), date(2026, 7, 24))
        hole = date(2026, 7, 8)
        s = mem_db()
        try:
            _seed_calendar(s, A_SHARE, window)
            _seed_quotes(s, A_SHARE, [d for d in window if d != hole])
            scan_asset(s, "daily.a_share", today=today, lookback_days=120)
            assert s.query(DataGap).filter(DataGap.status == "open").count() == 1

            _seed_quotes(s, A_SHARE, [hole])            # 补上了
            scan_asset(s, "daily.a_share", today=today, lookback_days=120)
            row = s.query(DataGap).filter(DataGap.gap_date == hole).one()
        finally:
            s.close()

        assert row.status == "filled"
        assert row.filled_at is not None


class TestRecheckBaselineIsNotSelfReferential:
    """🔴 回查基线必须用**完整扫描窗口**，不能只探缺口那几天。

    实测踩到（2026-07-31）：美股 07-27/28 只有 5343 只（正常 11000+）。
        扫描时  90 天窗口 → baseline=11324, 阈值 9059 → 报缺口 ✓
        回查时  只看那 2 天 → baseline= 5339, 阈值 4271 → 判「已补上」✗
    **基线被缺口自己拉低了** —— 判据取自被判对象自身，同「陈旧日历掩盖陈旧数据」
    是一类病。后果：每 6 小时白补一次 8 分钟的死循环，且 `permanent` 反证永不触发。
    """

    def _seed(self, session, window, full_days, thin_days, full_n=100, thin_n=45):
        from data_engine.storage.models import DailyQuote, TradingCalendar
        for d in window:
            session.add(TradingCalendar(market=US_STOCK, cal_date=d, source="test"))
        for d in full_days:
            for i in range(full_n):
                session.add(DailyQuote(symbol=f"us-{i}", market=US_STOCK, date=d,
                                       open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
        for d in thin_days:
            for i in range(thin_n):
                session.add(DailyQuote(symbol=f"us-{i}", market=US_STOCK, date=d,
                                       open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
        session.commit()

    def test_thin_day_not_declared_filled(self, mem_db, monkeypatch):
        """补完之后那天仍然只有一半数据 → 必须判「仍缺」，不许判 filled。"""
        import dataclasses

        from data_engine import gap_engine
        from data_engine.registry import get_asset
        from data_engine.storage.models import DataGap

        today = date(2026, 7, 31)
        window = _weekdays(date(2026, 5, 1), today)
        thin = [date(2026, 7, 27), date(2026, 7, 28)]
        full = [d for d in window if d not in thin]

        s = mem_db()
        self._seed(s, window, full, thin)
        for d in thin:
            s.add(DataGap(asset="daily.us_stock", market=US_STOCK, gap_date=d,
                          status="filling", confidence="certain", attempts=1))
        s.commit()
        s.close()

        # runner 什么都不做 —— 模拟「补了，但数据源那两天就是只有一半」
        stub = dataclasses.replace(get_asset("daily.us_stock"),
                                   runner=lambda ctx: iter(()))
        monkeypatch.setattr(gap_engine, "get_asset", lambda _k: stub)
        monkeypatch.setattr(gap_engine, "get_session", mem_db)
        monkeypatch.setattr(gap_engine, "market_today", lambda _m: today)

        result = gap_engine.fill_asset_gaps("daily.us_stock", thin)

        assert result["filled"] == [], (
            "补完仍只有一半数据却判成 filled —— 回查基线被缺口自己拉低了"
        )
        assert set(result["still_missing"]) == {d.isoformat() for d in thin}

    def _run_fill(self, mem_db, monkeypatch, today, gap_dates):
        import dataclasses

        from data_engine import gap_engine
        from data_engine.registry import get_asset

        stub = dataclasses.replace(get_asset("daily.us_stock"),
                                   runner=lambda ctx: iter(()))
        monkeypatch.setattr(gap_engine, "get_asset", lambda _k: stub)
        monkeypatch.setattr(gap_engine, "get_session", mem_db)
        monkeypatch.setattr(gap_engine, "market_today", lambda _m: today)
        return gap_engine.fill_asset_gaps("daily.us_stock", gap_dates)

    def test_empty_day_becomes_permanent_at_max_attempts(self, mem_db, monkeypatch):
        """**一条都没有**且试满次数 → 反证为假期，标 permanent 不再重试。"""
        from data_engine import gap_engine
        from data_engine.storage.models import DataGap

        today = date(2026, 7, 31)
        window = _weekdays(date(2026, 5, 1), today)
        empty = [date(2026, 7, 27)]
        full = [d for d in window if d not in empty]

        s = mem_db()
        self._seed(s, window, full, thin_days=[])   # empty 那天一条都不塞
        s.add(DataGap(asset="daily.us_stock", market=US_STOCK, gap_date=empty[0],
                      status="open", confidence="certain",
                      attempts=gap_engine.MAX_ATTEMPTS - 1))
        s.commit()
        s.close()

        result = self._run_fill(mem_db, monkeypatch, today, empty)
        assert result["marked_permanent"] == [empty[0].isoformat()], (
            "空白日试满次数却没标 permanent —— 这个洞会被无限重试下去"
        )

    def test_partial_day_never_becomes_permanent(self, mem_db, monkeypatch):
        """🔴 **有数据但不全** → 哪怕试满次数也**不许**标 permanent。

        `permanent` 的语义是「那天不是交易日」，而假期不可能有任何 bar。
        实测现场美股 07-27 有 5343 只（正常 11000+）—— 那是数据源限速拉不全，
        不是假期。标了 permanent 就等于永久销号，再也不会被补。
        """
        from data_engine import gap_engine
        from data_engine.storage.models import DataGap

        today = date(2026, 7, 31)
        window = _weekdays(date(2026, 5, 1), today)
        thin = [date(2026, 7, 27)]
        full = [d for d in window if d not in thin]

        s = mem_db()
        self._seed(s, window, full, thin)           # 那天只有一半数据
        s.add(DataGap(asset="daily.us_stock", market=US_STOCK, gap_date=thin[0],
                      status="open", confidence="certain",
                      attempts=gap_engine.MAX_ATTEMPTS + 5))   # 试了很多次
        s.commit()
        s.close()

        result = self._run_fill(mem_db, monkeypatch, today, thin)
        assert result["marked_permanent"] == [], (
            "「限速拉不全」被误判成「那天是假期」并永久销号了"
        )
        assert result["still_missing"] == [thin[0].isoformat()]
    def test_limit_up_uses_presence_not_coverage(self):
        """涨停池家数天天剧烈波动（30~300 家），拿 0.8×中位数卡它会把弱势日全误报。"""
        from data_engine.registry import get_asset

        assert get_asset("limit_up.a_share").gap_mode == "presence"
        assert get_asset("daily.a_share").gap_mode == "coverage"

    def test_signals_opted_out_of_generic_scan(self):
        """信号有自带的 `detect_signal_gaps`，不该再套一层通用扫描（两套判据会打架）。"""
        from data_engine.registry import get_asset

        assert get_asset("signals").supports_gap_fill is False
        assert get_asset("signals").gap_probe is None
