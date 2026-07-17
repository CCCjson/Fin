"""启动补跑的判定逻辑 —— 纯规则测试，零 fixture 零 mock 零 DB。

**为什么有这套东西**（2026-07-17，别当成过度设计）：cron 只在进程活着的那一刻
触发，而本项目后端跟着桌面 App 起停。15:35 那会儿后端不在 = 那天数据永久丢失，
**且无声无息**。实测连丢 6 个交易日（07-10 起全市场只剩 2 只票）没有任何告警。
`misfire_grace_time` 兜不住这个（内存 jobstore 下过去的触发点根本不进视野）。
"""
import os
import sys
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import data_engine.daily_pipeline_scheduler as sched_mod  # noqa: E402
from data_engine.daily_pipeline_scheduler import (  # noqa: E402
    _AUTO_HOUR,
    _AUTO_MINUTE,
    DailyPipelineScheduler,
    _last_expected_trading_day,
)
from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import DailyQuote, StockInfo  # noqa: E402

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
