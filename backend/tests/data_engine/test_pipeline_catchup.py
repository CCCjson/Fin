"""启动补跑的判定逻辑测试。

分三层：① 纯规则（`_last_expected_trading_day`，零 DB）；② 内存库（`db` fixture，
测 `_coverage_on` 日线覆盖率口径）；③ 下游新鲜度（`db_threadsafe` fixture，测
`_downstream_frontier` + `_catchup_job` 会不会因下游落后而触发补跑）。

**为什么有这套东西**（2026-07-17，别当成过度设计）：cron 只在进程活着的那一刻
触发，而本项目后端跟着桌面 App 起停。15:35 那会儿后端不在 = 那天数据永久丢失，
**且无声无息**。实测连丢 6 个交易日（07-10 起全市场只剩 2 只票）没有任何告警。
`misfire_grace_time` 兜不住这个（内存 jobstore 下过去的触发点根本不进视野）。

**下游那层（2026-07-20 加）**：日线覆盖率只量得到日线，量不到信号/追踪/涨停/估值
这些下游步骤跑没跑。曾踩过手动只补日线把覆盖率顶到 99%、下游却永远卡在一周前 ——
`_downstream_frontier` + `_catchup_job` 的 OR 判定就是堵这个洞的。
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import data_engine.daily_pipeline_scheduler as sched_mod  # noqa: E402
from data_engine.daily_pipeline_scheduler import (  # noqa: E402
    _AUTO_HOUR,
    _AUTO_MINUTE,
    _SIGNAL_BACKFILL_MAX_LOOKBACK,
    _SIGNAL_BACKFILL_MIN_LOOKBACK,
    DailyPipelineScheduler,
    _last_expected_trading_day,
    _weekday_span,
)
from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import DailyQuote, Signal, StockInfo  # noqa: E402

# 2026-07-17 是周五；07-18 周六、07-19 周日、07-20 周一


def test_after_close_today_counts():
    """周五收盘后 → 今天就该有数据了。"""
    d = _last_expected_trading_day(datetime(2026, 7, 17, 16, 0))
    assert d.isoformat() == "2026-07-17"


def test_before_close_falls_back_to_yesterday():
    """周五盘中/开盘前 → 今天还不该有完整数据，只能指望昨天（周四）。"""
    d = _last_expected_trading_day(datetime(2026, 7, 17, 9, 30))
    assert d.isoformat() == "2026-07-16"


def test_exactly_at_trigger_minute_counts_today():
    """恰好 15:35（cron 触发点）→ 算今天。边界闭在哪要钉死。"""
    d = _last_expected_trading_day(datetime(2026, 7, 17, _AUTO_HOUR, _AUTO_MINUTE))
    assert d.isoformat() == "2026-07-17"


def test_one_minute_before_trigger_does_not_count_today():
    d = _last_expected_trading_day(datetime(2026, 7, 17, _AUTO_HOUR, _AUTO_MINUTE - 1))
    assert d.isoformat() == "2026-07-16"


def test_weekend_walks_back_to_friday():
    """周末开 App → 该有的是周五的数据，不是周六的。"""
    assert _last_expected_trading_day(datetime(2026, 7, 18, 20, 0)).isoformat() == "2026-07-17"
    assert _last_expected_trading_day(datetime(2026, 7, 19, 20, 0)).isoformat() == "2026-07-17"


def test_monday_morning_walks_back_to_friday():
    """周一早上开 App（还没收盘）→ 往前找到周五，不是周日。"""
    d = _last_expected_trading_day(datetime(2026, 7, 20, 9, 0))
    assert d.isoformat() == "2026-07-17"


def test_holiday_false_positive_is_acceptable_by_design():
    """**刻意不查节假日**：这条测试是在钉「我们接受假阳性」这个决定。

    假期里判出的「应有交易日」其实没开市 → 补跑会空转一次（DailyUpdater 自己会
    探真实交易日，数据全的话所有股票落进 already_fresh，几秒就 complete）。
    用几秒空转换掉一整套交易日历的维护成本。

    反过来漏判是**永久**的 —— 所以宁可多空转，不可漏掉。
    """
    # 假设 07-17 是节假日：函数照样返回它（不知道也不该知道）
    assert _last_expected_trading_day(datetime(2026, 7, 17, 16, 0)).isoformat() == "2026-07-17"


@pytest.fixture
def db(monkeypatch):
    """内存库，绝不碰生产 data/market.db。

    patch **被测模块里查到的那个名字** —— `_coverage_on` 是函数内延迟 import
    `get_session`，所以要 patch database 模块本身的属性。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    import data_engine.storage.database as db_mod
    monkeypatch.setattr(db_mod, "get_session", lambda: session_factory())
    return session_factory


def _seed(s, n_stocks, n_with_fresh_data, day, *, leader_days_ahead=0):
    for i in range(n_stocks):
        sym = f"6{i:05d}.SH"
        s.add(StockInfo(symbol=sym, name=f"股{i}", market="a_share",
                        is_active=1, stock_type="stock"))
        if i < n_with_fresh_data:
            s.add(DailyQuote(symbol=sym, market="a_share", date=day,
                             open=10, high=10, low=10, close=10, volume=1))
    # 「领跑者」：个别票的数据比别人新（库里真实存在这种票）
    if leader_days_ahead:
        s.add(DailyQuote(symbol="600000.SH", market="a_share",
                         date=day + timedelta(days=leader_days_ahead),
                         open=10, high=10, low=10, close=10, volume=1))
    s.commit()


def test_coverage_catches_a_dead_day_that_max_date_would_miss(db):
    """⚠️ **核心回归**：`max(date)` 会撒谎，覆盖率不会。

    造一个真实场景：5000 只票全部停在一周前，只有 1 只「领跑者」有今天的数据
    （库里真的有这种票）。此时：
      - `max(DailyQuote.date)` = 今天 → 判定「数据新鲜」→ **整个市场烂了也不补跑**
      - 覆盖率 = 1/5000 = 0.02% → 判定「该补」✅

    这就是每日链连丢 6 个交易日没人发现的同一类病：**一个数看着没问题，
    其实什么都没检查。** 别把这个检查改回 max()。
    """
    today = date(2026, 7, 17)
    s = db()
    # 5000 只票，没有一只有今天的数据；只有领跑者 600000.SH 有
    _seed(s, 5000, 0, today - timedelta(days=7), leader_days_ahead=7)
    s.close()

    # max() 的视角：一切正常
    s = db()
    assert s.query(func.max(DailyQuote.date)).scalar() == today, "max() 说数据是今天的"
    s.close()

    # 覆盖率的视角：市场其实全烂了
    have, total = DailyPipelineScheduler._coverage_on(today)
    assert total == 5000
    assert have == 1, "只有领跑者一只票有今天的数据"
    assert have / total * 100 < sched_mod._CATCHUP_COVERAGE_PCT, "必须判定为「该补跑」"


def test_coverage_says_fine_when_market_is_actually_fresh(db):
    """正常情况：绝大多数票都有当天数据 → 不补跑（别天天空转）。"""
    today = date(2026, 7, 17)
    s = db()
    _seed(s, 5000, 4900, today)      # 98%，剩下 2% 是停牌/退市/新股，正常
    s.close()

    have, total = DailyPipelineScheduler._coverage_on(today)
    assert have / total * 100 >= sched_mod._CATCHUP_COVERAGE_PCT


def test_coverage_ignores_other_markets(db):
    """必须带 market 过滤 —— 港美股混进 A 股分母是复现过的事故。

    （见「日线覆盖率 300% bug」：DailyUpdater 分子漏 market 过滤，港美股混进来。）
    """
    today = date(2026, 7, 17)
    s = db()
    _seed(s, 100, 0, today)
    # 港美股有今天的数据，但它们不该影响 A 股的覆盖率判定
    for i in range(500):
        s.add(DailyQuote(symbol=f"{i:05d}.HK", market="hk_stock", date=today,
                         open=10, high=10, low=10, close=10, volume=1))
    s.commit()
    s.close()

    have, total = DailyPipelineScheduler._coverage_on(today)
    assert total == 100, "分母只数活跃 A 股"
    assert have == 0, "分子不许把港美股算进来"


def test_coverage_excludes_etf_from_denominator(db):
    """ETF 已停用（2026-07-09 拍板不交易 ETF），不该进分母把覆盖率拉低。"""
    today = date(2026, 7, 17)
    s = db()
    _seed(s, 100, 100, today)
    for i in range(50):   # 一堆没数据的 ETF
        s.add(StockInfo(symbol=f"5{i:05d}.SH", name=f"ETF{i}", market="a_share",
                        is_active=1, stock_type="etf"))
    s.commit()
    s.close()

    have, total = DailyPipelineScheduler._coverage_on(today)
    assert total == 100, "ETF 不进分母"
    assert have / total * 100 == 100.0


def test_overseas_catchup_threshold_is_blunter_than_a_share():
    """港美股补跑门槛必须**明显钝于** A 股 —— 假阳性代价不是一个量级。

    A 股误判 = 几秒空转；港美股误判 = 10-15 分钟白打 Yahoo（~16k 只逐批空手而归）。
    且港美股各有独立假期，没交易日历就分不清「今天是假期」和「job 没跑」。
    """
    assert sched_mod._OVERSEAS_CATCHUP_STALE_DAYS >= 3, (
        "调低这个值会让每个港股/美股假期都白跑一次 10-15 分钟的全量拉取"
    )


def test_overseas_job_is_registered_and_separate_from_the_chain(monkeypatch):
    """港美股必须是**独立 job**，不是链的一步。

    并进链里有两个问题：① 港股 16:00 HKT 才收盘，链 15:35 跑拿到的是半截子；
    ② ~16k 只跑 10-15 分钟，会把链后面的信号回补/追踪/估值刷新全部推迟。
    """
    s = DailyPipelineScheduler()
    jobs = {}

    class _FakeScheduler:
        def add_job(self, fn, trigger, *, id, name, **kw):
            jobs[id] = (name, trigger)

        def start(self):
            pass

    monkeypatch.setattr(s, "_get_scheduler", lambda: _FakeScheduler())
    s.start()

    assert sched_mod.OVERSEAS_JOB_ID in jobs, "港美股 job 没注册"
    assert sched_mod.JOB_ID in jobs, "A 股主链 job 没注册"
    assert jobs[sched_mod.OVERSEAS_JOB_ID][1] is not jobs[sched_mod.JOB_ID][1], \
        "两个 job 必须各自独立的 trigger"


def test_overseas_cron_is_after_hk_close():
    """港股 16:00 HKT 收盘 —— 早于此跑拿到的是没收盘的半截子日线。"""
    assert (sched_mod._OVERSEAS_HOUR, sched_mod._OVERSEAS_MINUTE) >= (16, 0), (
        "港美股 job 不能早于港股 16:00 收盘"
    )
    # 也必须晚于 A 股链，否则两个重活撞一起
    assert (sched_mod._OVERSEAS_HOUR, sched_mod._OVERSEAS_MINUTE) > (_AUTO_HOUR, _AUTO_MINUTE)


def test_catchup_registered_even_if_only_overseas_enabled(monkeypatch):
    """A 股自动更新被关掉时，港美股的补跑不该跟着失踪。"""
    s = DailyPipelineScheduler()
    s._enabled = False
    s._overseas_enabled = True
    jobs = []

    class _FakeScheduler:
        def add_job(self, fn, trigger, *, id, name, **kw):
            jobs.append(id)

        def start(self):
            pass

    monkeypatch.setattr(s, "_get_scheduler", lambda: _FakeScheduler())
    s.start()

    assert sched_mod.CATCHUP_JOB_ID in jobs
    assert sched_mod.JOB_ID not in jobs, "A 股关了就不该注册主链 job"


def test_misfire_grace_does_not_cover_a_dead_process():
    """回归钉子：别再有人以为 misfire_grace_time 能兜「进程没起」。

    这里直接验 APScheduler 的真实语义 —— 新建 job 的 next_run_time 从当下往后算，
    过去的触发点不会被补跑。（原代码注释写「服务重启错过触发点，1 小时内仍补跑」
    是假的，而且大概率就是连丢 6 天没人发现的原因。）
    """
    import pytz
    from apscheduler.triggers.cron import CronTrigger

    tz = pytz.timezone("Asia/Shanghai")
    trigger = CronTrigger(hour=_AUTO_HOUR, minute=_AUTO_MINUTE, day_of_week="mon-fri",
                          timezone=tz)
    # 周五 16:24 —— 今天的 15:35 刚过去 49 分钟，远在 misfire_grace_time=3600 之内
    now = tz.localize(datetime(2026, 7, 17, 16, 24))
    nxt = trigger.get_next_fire_time(None, now)
    assert nxt.date().isoformat() == "2026-07-20", (
        "next_run 应该是下周一 —— 今天错过的那次不会被补跑。"
        "这就是为什么必须有 _catchup_job。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 下游新鲜度（2026-07-20）：日线覆盖率之外，还要看信号/追踪/涨停/估值这些
# **下游步骤**跑没跑。堵的是「手动只补日线把覆盖率顶到 99%、下游却永远卡在
# 一周前」这个洞（Bug 2）。
# ─────────────────────────────────────────────────────────────────────────────


def _seed_signals(s, day, n=50):
    """往库里塞 `day` 当天的信号（整批全市场生成，模拟真实回补）。"""
    for i in range(n):
        s.add(Signal(symbol=f"6{i:05d}.SH", date=day, signal_type="BUY",
                     strength=0.8, price=10.0, signal_id=f"sig-{day.isoformat()}-{i}"))
    s.commit()


# ---- _signal_backfill_lookback：纯计算，零 DB ----

def test_lookback_floors_at_min_when_gap_is_tiny():
    """标杆就是今天/昨天（gap 很小）→ 落到下限，保持原 5 天行为。"""
    today = date(2026, 7, 17)
    assert DailyPipelineScheduler._signal_backfill_lookback(today, today) == \
        _SIGNAL_BACKFILL_MIN_LOOKBACK
    # gap=1 → 1+3=4 仍 < MIN(5) → 被下限托住
    assert DailyPipelineScheduler._signal_backfill_lookback(
        today - timedelta(days=1), today) == _SIGNAL_BACKFILL_MIN_LOOKBACK


def test_lookback_adapts_to_actual_gap():
    """停机多日 → 窗口跟着放大（gap+3），这正是写死 5 天够不着的老缺口。"""
    today = date(2026, 7, 17)
    # 落后 8 天 → 8+3=11，介于 MIN 与 MAX 之间，原样返回
    assert DailyPipelineScheduler._signal_backfill_lookback(
        today - timedelta(days=8), today) == 11


def test_lookback_caps_at_max_for_huge_gap():
    """标杆是远古 → 封在 MAX，别一次触发全历史回填。"""
    today = date(2026, 7, 17)
    assert DailyPipelineScheduler._signal_backfill_lookback(
        today - timedelta(days=9999), today) == _SIGNAL_BACKFILL_MAX_LOOKBACK


def test_lookback_uses_max_when_table_empty():
    """signals 表空（sig_latest is None）→ 用 MAX 兜底。"""
    assert DailyPipelineScheduler._signal_backfill_lookback(None, date(2026, 7, 17)) == \
        _SIGNAL_BACKFILL_MAX_LOOKBACK


# ---- _downstream_frontier：查 max(Signal.date) ----

def test_downstream_frontier_returns_max_signal_date(db):
    """标杆 = 最新一批信号的日期（signals 整批生成，max 安全）。"""
    s = db()
    _seed_signals(s, date(2026, 7, 9))
    _seed_signals(s, date(2026, 7, 7))
    s.close()
    assert DailyPipelineScheduler._downstream_frontier() == date(2026, 7, 9)


def test_downstream_frontier_is_none_when_empty(db):
    """表空 → None（调用侧据此判「落后」并用 MAX 兜底 lookback）。"""
    assert DailyPipelineScheduler._downstream_frontier() is None


# ---- _catchup_job：日线新鲜但下游落后必须触发（Bug 2 核心回归）----

@pytest.fixture
def db_threadsafe(monkeypatch):
    """跨线程共享的内存库。

    `_catchup_job` 用 `run_in_executor` 把 `_coverage_on`/`_downstream_frontier`
    丢到线程池，而默认 `:memory:` 每个连接一个独立库、worker 线程看不到主线程 seed
    的数据。StaticPool + check_same_thread=False = 全线程共用同一条连接、同一个库。
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    import data_engine.storage.database as db_mod
    monkeypatch.setattr(db_mod, "get_session", lambda: session_factory())
    return session_factory


def _run_catchup(monkeypatch, *, expected):
    """跑一次 `_catchup_job`，返回它有没有触发整条链（`_daily_pipeline_job`）。

    港美股补跑与真链本体都换成 no-op —— 这里只验「触发决策」，不跑真链。
    """
    sch = DailyPipelineScheduler()
    sch._enabled = True
    sch._is_updating = False
    monkeypatch.setattr(sched_mod, "_last_expected_trading_day", lambda now: expected)

    called = []

    async def _noop_overseas():
        pass

    async def _fake_pipeline():
        called.append(True)

    monkeypatch.setattr(sch, "_overseas_catchup", _noop_overseas)
    monkeypatch.setattr(sch, "_daily_pipeline_job", _fake_pipeline)
    asyncio.run(sch._catchup_job())
    return bool(called)


def test_catchup_triggers_when_signals_lag_behind_fresh_daily(db_threadsafe, monkeypatch):
    """⚠️ **Bug 2 核心回归**：日线 100% 新鲜，但信号卡在 8 天前 → 必须补跑。

    没有下游检查时（旧代码只看覆盖率），这里会判「无需补跑」，下游永远卡死。
    """
    expected = date(2026, 7, 17)
    s = db_threadsafe()
    _seed(s, 100, 100, expected)                        # 日线覆盖率 100%
    _seed_signals(s, expected - timedelta(days=8))      # 但信号落后 8 天
    s.close()

    assert _run_catchup(monkeypatch, expected=expected) is True, \
        "日线新鲜但下游落后 → 必须触发补跑（别把这个检查删回只看覆盖率）"


def test_catchup_skips_when_both_daily_and_downstream_fresh(db_threadsafe, monkeypatch):
    """日线与下游都追平到 expected → 不补跑（别天天空转/thrashing）。"""
    expected = date(2026, 7, 17)
    s = db_threadsafe()
    _seed(s, 100, 100, expected)
    _seed_signals(s, expected)                          # 信号也到 expected
    s.close()

    assert _run_catchup(monkeypatch, expected=expected) is False, \
        "都新鲜就不该触发"


def test_catchup_triggers_when_signals_table_empty(db_threadsafe, monkeypatch):
    """signals 表空（sig_latest is None）→ 判落后 → 触发补跑。"""
    expected = date(2026, 7, 17)
    s = db_threadsafe()
    _seed(s, 100, 100, expected)                        # 日线新鲜，但没有任何信号
    s.close()

    assert _run_catchup(monkeypatch, expected=expected) is True, \
        "下游从来没跑过 → 必须补跑"


# ─────────────────────────────────────────────────────────────────────────────
# 港美股补跑的周末感知（2026-07-20）：按工作日算落后天数，别把周末误当落后。
# 实测过：周一早上港/美股停在上周五，裸算自然日 = 3 天 → 误触发一次 10-15min 空拉。
# ─────────────────────────────────────────────────────────────────────────────


def test_weekday_span_treats_friday_to_monday_as_one():
    """周五→周一只差 1 个工作日（周末不算），不是裸算的 3 天。"""
    assert _weekday_span(date(2026, 7, 17), date(2026, 7, 20)) == 1   # Fri→Mon
    assert _weekday_span(date(2026, 7, 16), date(2026, 7, 20)) == 2   # Thu→Mon
    assert _weekday_span(date(2026, 7, 17), date(2026, 7, 17)) == 0   # 同日
    assert _weekday_span(date(2026, 7, 20), date(2026, 7, 17)) == 0   # end<=start


def test_weekday_span_counts_real_multiday_outage():
    """真断多天（07-08 → 07-20）→ 工作日数够大，仍会触发补跑。"""
    # 07-09,10,13,14,15,16,17,20 = 8 个工作日（跳过两个周末）
    assert _weekday_span(date(2026, 7, 8), date(2026, 7, 20)) == 8


def _run_overseas_catchup(monkeypatch, *, frontier, today):
    """跑一次 `_overseas_catchup`，返回它有没有触发港美股补跑（`_overseas_job`）。"""
    class _FakeDate(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(sched_mod, "date", _FakeDate)

    sch = DailyPipelineScheduler()
    sch._overseas_enabled = True
    sch._overseas_updating = False
    monkeypatch.setattr(sch, "_overseas_frontier", lambda: dict(frontier))

    called = []

    async def _fake_job():
        called.append(True)

    monkeypatch.setattr(sch, "_overseas_job", _fake_job)
    asyncio.run(sch._overseas_catchup())
    return bool(called)


def test_overseas_catchup_does_not_fire_on_monday_after_weekend(monkeypatch):
    """⚠️ #1 回归：周一早上港股停上周五、美股停上周四 = 正常，别补跑。"""
    fired = _run_overseas_catchup(
        monkeypatch,
        frontier={"hk_stock": date(2026, 7, 17), "us_stock": date(2026, 7, 16)},
        today=date(2026, 7, 20),   # 周一
    )
    assert fired is False, "周末造成的 1-2 工作日落后不该触发 10-15min 空拉"


def test_overseas_catchup_still_fires_on_real_outage(monkeypatch):
    """真断多天（港股停 07-08，落后 8 个工作日）→ 仍要补。"""
    fired = _run_overseas_catchup(
        monkeypatch,
        frontier={"hk_stock": date(2026, 7, 8), "us_stock": date(2026, 7, 8)},
        today=date(2026, 7, 20),
    )
    assert fired is True, "真落后 >= 3 工作日必须补跑"
