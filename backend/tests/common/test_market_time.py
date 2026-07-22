"""`common.market_time` 行为基线 —— 四市场时区、市场日边界、序列化。

这些断言钉住的是**跨市场「今天」不是同一天**这件事。项目在此之前全用服务器本地
`date.today()`，A 股/港股恰好对，美股每天有约 12 小时在问一个还没发生的交易日，
crypto 则与自己 UTC 落库的行情差 8 小时。
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK
from common.market_time import (
    MARKET_TZ,
    UTC,
    market_day_bounds,
    market_day_of,
    market_now,
    market_today,
    to_market_tz,
    tz_of,
    utc_iso,
    utc_now,
)

pytestmark = pytest.mark.baseline


class TestTimezoneMapping:
    def test_four_markets_have_a_timezone(self):
        assert set(MARKET_TZ) == {A_SHARE, HK_STOCK, US_STOCK, CRYPTO}

    def test_canonical_mapping(self):
        assert str(tz_of(A_SHARE)) == "Asia/Shanghai"
        assert str(tz_of(HK_STOCK)) == "Asia/Shanghai"
        assert str(tz_of(US_STOCK)) == "America/New_York"
        assert str(tz_of(CRYPTO)) == "UTC"

    def test_aliases_are_normalized(self):
        """任意历史写法都得能进来 —— 真源是 common.market.normalize_market。"""
        assert tz_of("us") is tz_of(US_STOCK)
        assert tz_of("美股") is tz_of(US_STOCK)
        assert tz_of("BN") is tz_of(CRYPTO)
        assert tz_of("加密货币") is tz_of(CRYPTO)

    def test_unknown_falls_back_to_a_share(self):
        """与全项目 fallback 口径一致，不抛异常。"""
        assert tz_of(None) is tz_of(A_SHARE)
        assert tz_of("火星股") is tz_of(A_SHARE)


class TestUtcNow:
    def test_is_naive(self):
        """存储铁律：库里全是 naive UTC，绝不存 aware（SQLite 存不住 tz）。"""
        assert utc_now().tzinfo is None

    def test_tracks_real_utc(self):
        assert abs((datetime.now(tz=UTC).replace(tzinfo=None) - utc_now()).total_seconds()) < 5

    def test_market_now_is_aware(self):
        assert market_now(US_STOCK).tzinfo is not None


class TestMarketDayOf:
    """一个存储时刻属于哪个市场日 —— 这是 `dt.date()` 会答错的地方。"""

    def test_us_afternoon_is_still_the_same_us_day(self):
        """美东 07-21 14:00 = UTC 07-21 18:00。UTC 日和美股日**恰好同一天**。"""
        dt = datetime(2026, 7, 21, 18, 0)          # naive UTC
        assert market_day_of(dt, US_STOCK) == date(2026, 7, 21)

    def test_us_evening_crosses_utc_midnight(self):
        """美东 07-21 21:00 = UTC 07-22 01:00 —— 裸 `.date()` 会说 07-22，错一天。"""
        dt = datetime(2026, 7, 22, 1, 0)           # naive UTC
        assert dt.date() == date(2026, 7, 22)      # 这就是被修掉的行为
        assert market_day_of(dt, US_STOCK) == date(2026, 7, 21)

    def test_shanghai_early_morning_crosses_utc_midnight(self):
        """上海 07-22 03:00 = UTC 07-21 19:00 —— 裸 `.date()` 会说 07-21。

        crypto 那个「当日手续费恒为 0」的 bug 就是这个形状（列 UTC、today 本地）。
        """
        dt = datetime(2026, 7, 21, 19, 0)          # naive UTC
        assert dt.date() == date(2026, 7, 21)
        assert market_day_of(dt, A_SHARE) == date(2026, 7, 22)

    def test_crypto_day_equals_utc_day(self):
        dt = datetime(2026, 7, 21, 19, 0)
        assert market_day_of(dt, CRYPTO) == dt.date()

    def test_accepts_aware_input(self):
        aware = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)
        assert market_day_of(aware, US_STOCK) == date(2026, 7, 21)

    def test_to_market_tz_renders_wall_clock(self):
        assert to_market_tz(datetime(2026, 7, 22, 1, 0), US_STOCK).hour == 21


class TestMarketDayBounds:
    def test_half_open_and_utc_naive(self):
        start, end = market_day_bounds(CRYPTO, date(2026, 7, 22))
        assert (start, end) == (datetime(2026, 7, 22, 0, 0), datetime(2026, 7, 23, 0, 0))
        assert start.tzinfo is None and end.tzinfo is None

    def test_shanghai_day_maps_to_previous_utc_evening(self):
        """上海 07-22 这一天 = UTC 07-21 16:00 起 —— 正是那 8 小时黑洞的来源。"""
        start, end = market_day_bounds(A_SHARE, date(2026, 7, 22))
        assert start == datetime(2026, 7, 21, 16, 0)
        assert end == datetime(2026, 7, 22, 16, 0)

    def test_us_day_maps_to_utc_afternoon(self):
        start, end = market_day_bounds(US_STOCK, date(2026, 7, 22))
        assert start == datetime(2026, 7, 22, 4, 0)      # EDT = UTC-4
        assert end == datetime(2026, 7, 23, 4, 0)

    @pytest.mark.parametrize("day,hours", [
        (date(2026, 3, 8), 23),      # 美东夏令时开始（3 月第二个周日），这天只有 23 小时
        (date(2026, 11, 1), 25),     # 美东夏令时结束（11 月第一个周日），这天有 25 小时
        (date(2026, 6, 1), 24),      # 对照组
    ])
    def test_us_dst_transition_days_are_not_24h(self, day, hours):
        start, end = market_day_bounds(US_STOCK, day)
        assert (end - start).total_seconds() / 3600 == hours

    def test_never_add_24h_to_the_utc_start(self):
        """⛔ 钉死实现方式：末端必须由「次日本地零点」换算，不能拿 UTC 起点 +24h。

        `ZoneInfo` 的加法是**墙钟算术**，所以在本地侧 `+timedelta(days=1)` 是安全的
        （它会自己吸收掉夏令时那 1 小时）；但换算成 UTC 之后再加就是绝对 24 小时，
        夏令时切换日会错 1 小时 —— 那一小时的成交会被算进隔壁那天。
        """
        start, end = market_day_bounds(US_STOCK, date(2026, 3, 8))
        assert end != start + timedelta(days=1)
        assert end == start + timedelta(hours=23)

    def test_defaults_to_market_today(self):
        start, _ = market_day_bounds(CRYPTO)
        assert start == datetime.combine(market_today(CRYPTO), datetime.min.time())

    def test_naive_local_storage_is_the_same_instant(self):
        """迁移期口径：naive_local 与 utc 指向**同一个绝对时刻**，只是换了张皮。

        不硬编码 +8 —— 服务器换时区这条断言依然成立。
        """
        local_tz = datetime.now().astimezone().tzinfo
        for market in (A_SHARE, US_STOCK, CRYPTO):
            u_start, u_end = market_day_bounds(market, date(2026, 7, 22))
            l_start, l_end = market_day_bounds(market, date(2026, 7, 22), storage="naive_local")
            assert l_start.replace(tzinfo=local_tz).astimezone(UTC).replace(tzinfo=None) == u_start
            assert l_end.replace(tzinfo=local_tz).astimezone(UTC).replace(tzinfo=None) == u_end


class TestUtcIso:
    def test_naive_gets_utc_offset(self):
        """带 offset 是关键：不带的话前端 new Date() 会按本地时区解析 UTC 值。"""
        assert utc_iso(datetime(2026, 7, 22, 2, 24, 53)) == "2026-07-22T02:24:53+00:00"

    def test_none_passes_through(self):
        assert utc_iso(None) is None

    def test_aware_is_preserved(self):
        aware = datetime(2026, 7, 22, 10, 24, 53, tzinfo=timezone(timedelta(hours=8)))
        assert utc_iso(aware) == "2026-07-22T10:24:53+08:00"

    def test_js_parses_it_back_to_the_same_instant(self):
        """模拟前端：带 offset 的串解析回来必须是同一时刻（这正是裸串做不到的）。"""
        dt = datetime(2026, 7, 22, 2, 24, 53)
        assert datetime.fromisoformat(utc_iso(dt)).astimezone(UTC).replace(tzinfo=None) == dt


class TestMarketTodayCrossMarket:
    def test_today_may_differ_across_markets(self):
        """不断言具体差几天（取决于跑测试的时刻），只钉住「各算各的」这条契约。"""
        days = {m: market_today(m) for m in (A_SHARE, US_STOCK, CRYPTO)}
        assert all(isinstance(d, date) for d in days.values())
        # 任意两个市场的「今天」相差不超过 1 天
        for a in days.values():
            for b in days.values():
                assert abs((a - b).days) <= 1
