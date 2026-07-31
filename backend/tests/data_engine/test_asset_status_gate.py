"""资产矩阵的两条防线 —— 都是「后台自己跑了几天」实测暴露出来的。

1. **`_dict_runner` 的键冲突**：被调函数返回的 dict 里若有 `market` 键，
   `**payload` 会撞成 `make_event() got multiple values for argument 'market'`。
   实测炸过：`refresh_calendar()` 返回 `{"market", "added", ...}` → 三个 calendar
   资产**每次定时跑都当场失败，连着三天没人发现**。

2. **陈旧的日历掩盖陈旧的数据**：判「落后几个交易日」用的是交易日历，日历自己
   停更时「latest 之后还有几个交易日」= 0 → 数据落后 3 天却显示 ok/behind=0。
   判据和被判对象来自同一条坏掉的链路，一起坏就一起「正常」——
   这正是整套设计要消灭的病（同 `_coverage_on` 那句「一个数看着没问题，
   其实什么都没检查」）。
"""
from datetime import date, timedelta

import pytest

from common.market import HK_STOCK

pytestmark = pytest.mark.baseline


@pytest.fixture
def mem_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data_engine.storage.models  # noqa: F401
    from data_engine.storage.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


class TestDictRunnerKeyCollision:
    def test_result_dict_may_contain_market_key(self):
        """被调函数返回 `market` 键时不许炸 —— calendar 资产就是这个形状。"""
        from data_engine.registry import RunContext, _dict_runner

        def call(_ctx):
            # 与 `trading_calendar.refresh_calendar()` 的真实返回形状一致
            return {"market": "hk_stock", "added": 12, "source": "index:^HSI",
                    "latest": "2026-07-30"}

        runner = _dict_runner("calendar.hk_stock", HK_STOCK, call,
                              field_map={"added": "records"})
        events = list(runner(RunContext()))

        assert [e["event"] for e in events] == ["start", "complete"]
        done = events[-1]
        assert done["market"] == HK_STOCK
        assert done["records"] == 12
        assert done["source"] == "index:^HSI"

    def test_result_dict_may_contain_event_and_asset_keys(self):
        """`event`/`asset` 同理 —— 三个保留键都不能撞。"""
        from data_engine.registry import RunContext, _dict_runner

        def call(_ctx):
            return {"event": "junk", "asset": "junk", "market": "junk", "ok": True}

        events = list(_dict_runner("x.y", None, call)(RunContext()))
        assert events[-1]["event"] == "complete"
        assert events[-1]["asset"] == "x.y"


class TestStaleCalendarDoesNotMaskStaleData:
    def _seed(self, session, cal_days, quote_days):
        from data_engine.storage.models import DailyQuote, TradingCalendar
        for d in cal_days:
            session.add(TradingCalendar(market=HK_STOCK, cal_date=d, source="test"))
        for d in quote_days:
            for i in range(100):
                session.add(DailyQuote(symbol=f"hk-{i}", market=HK_STOCK, date=d,
                                       open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
        session.commit()

    def test_behind_returns_none_when_calendar_itself_is_stale(self, mem_db):
        """日历自己停更 → 不能拿它当尺子，必须返回 None 而不是 0。"""
        from data_engine.asset_status import _behind_trading_days

        today = date(2026, 7, 31)
        stuck = date(2026, 7, 27)   # 日历和数据都停在这里
        cal = [d for d in (stuck - timedelta(days=i) for i in range(60))
               if d.weekday() < 5]
        s = mem_db()
        try:
            self._seed(s, cal, cal[:20])
            behind = _behind_trading_days(s, HK_STOCK, stuck, today)
        finally:
            s.close()

        assert behind is None, "日历自己停更了却还拿它算出 behind=0 —— 掩盖了真实落后"

    def test_fallback_flags_stale_when_calendar_unavailable(self, mem_db):
        """日历不可用时必须走独立兜底判据，**绝不能落到 ok**。"""
        from data_engine.asset_status import _fallback_stale

        today = date(2026, 7, 31)
        verdict = _fallback_stale(HK_STOCK, date(2026, 7, 27), today)

        assert verdict is not None
        assert verdict[0] == "stale"
        assert "交易日历不可用" in verdict[1]

    def test_fresh_data_still_ok_under_fallback(self):
        """兜底判据不能反过来把正常数据判成异常（周末 / 单日滞后要容忍）。"""
        from data_engine.asset_status import _fallback_stale

        # 周一 07-27，数据到上周五 07-24 —— 只差 1 个工作日，正常
        assert _fallback_stale(HK_STOCK, date(2026, 7, 24), date(2026, 7, 27)) is None

    def test_health_never_reports_ok_when_fallback_says_stale(self, mem_db):
        """端到端：behind 算不出来 + 兜底判 stale → health 必须是 stale。"""
        from data_engine.asset_status import _health
        from data_engine.registry import get_asset

        asset = get_asset("daily.hk_stock")
        health, reason = _health(
            asset, date(2026, 7, 27), {}, behind=None, count=100,
            fallback=("stale", "落后约 4 个工作日（交易日历不可用…）"),
        )
        assert health == "stale", "behind 算不出来时静默落到了 ok"
        assert "落后" in reason
