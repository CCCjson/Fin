"""港美股日线增量更新 —— 选票逻辑 + Yahoo 互斥锁。

⚠️ 全程内存库 + 不出网（yfinance 相关一律 mock），绝不碰生产 data/market.db。
"""
import os
import sys
import threading
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import data_engine.overseas_daily_updater as m  # noqa: E402
from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import DailyQuote, StockInfo  # noqa: E402

TODAY = date(2026, 7, 17)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(m, "get_session", lambda: factory())
    return factory


@pytest.fixture(autouse=True)
def _never_touch_yahoo(monkeypatch):
    """熔断：任何真出网都当场炸。

    本文件只测选票逻辑和锁，不该有任何 yfinance 调用漏出去。
    """
    def _boom(*a, **k):
        raise AssertionError("测试不许真打 Yahoo")
    monkeypatch.setattr(m.OverseasDailyUpdater, "_fetch_batch", _boom)
    monkeypatch.setattr(m.OverseasDailyUpdater, "_precheck", _boom)


def _seed(s, market, symbol, name="X", stype="stock", latest=None, active=1):
    s.add(StockInfo(symbol=symbol, name=name, market=market,
                    is_active=active, stock_type=stype))
    if latest:
        s.add(DailyQuote(symbol=symbol, market=market, date=latest,
                         open=1, high=1, low=1, close=1, volume=1))


def _plan(db, market="hk_stock"):
    s = db()
    try:
        return m.OverseasDailyUpdater()._plan(s, market, TODAY)
    finally:
        s.close()


def test_picks_stale_symbols(db):
    s = db()
    _seed(s, "hk_stock", "00700.HK", latest=TODAY - timedelta(days=9))
    s.commit()
    s.close()

    todo, latest, stats = _plan(db)
    assert todo == ["00700.HK"]
    assert stats["todo"] == 1
    assert stats["start"] == (TODAY - timedelta(days=8)).isoformat(), "起点 = 最落后那只的次日"
    assert latest["00700.HK"] == TODAY - timedelta(days=9)


def test_skips_already_fresh(db):
    s = db()
    _seed(s, "hk_stock", "00700.HK", latest=TODAY)
    s.commit()
    s.close()

    todo, _, stats = _plan(db)
    assert todo == []
    assert stats["skipped_fresh"] == 1
    assert "start" not in stats


def test_skips_symbols_with_no_data_at_all(db):
    """一根 bar 都没有的票是**深历史的活**，不是增量的。

    让增量去拉十年数据会把一次运行拖死，而且深历史有断点续跑/confirmed_no_data
    那一套，增量没有。
    """
    s = db()
    _seed(s, "hk_stock", "09999.HK", latest=None)   # 零数据
    _seed(s, "hk_stock", "00700.HK", latest=TODAY - timedelta(days=3))
    s.commit()
    s.close()

    todo, _, stats = _plan(db)
    assert todo == ["00700.HK"]
    assert stats["skipped_no_data"] == 1


def test_excluded_types_are_not_fetched(db):
    """港股人民币柜台/债券、美股杠杆 ETF 一律不抓 —— 白烧请求。

    ⚠️ 港美股靠 **stock_type** 排除，`is_active` 全是 1（美股 914 只 excluded_*
    也是 is_active=1），所以过滤条件不能只看 is_active。
    """
    s = db()
    base = TODAY - timedelta(days=5)
    _seed(s, "hk_stock", "00700.HK", stype="stock", latest=base)
    _seed(s, "hk_stock", "03032.HK", stype="etf", latest=base)
    _seed(s, "hk_stock", "89988.HK", stype="excluded_rmb_counter", latest=base)
    _seed(s, "hk_stock", "40939.HK", stype="excluded_bond_note", latest=base)
    s.commit()
    s.close()

    todo, _, stats = _plan(db)
    assert sorted(todo) == ["00700.HK", "03032.HK"], "只抓 stock + etf"
    assert stats["universe"] == 2


def test_inactive_still_excluded(db):
    s = db()
    _seed(s, "hk_stock", "00700.HK", latest=TODAY - timedelta(days=5), active=0)
    s.commit()
    s.close()
    assert _plan(db)[0] == []


def test_other_market_not_touched(db):
    """跑港股时绝不碰美股/A 股 —— market 过滤不统一是复现过的事故。"""
    s = db()
    base = TODAY - timedelta(days=5)
    _seed(s, "hk_stock", "00700.HK", latest=base)
    _seed(s, "us_stock", "AAPL", latest=base)
    _seed(s, "a_share", "600519.SH", latest=base)
    s.commit()
    s.close()

    assert _plan(db, "hk_stock")[0] == ["00700.HK"]
    assert _plan(db, "us_stock")[0] == ["AAPL"]


def test_lookback_is_capped(db):
    """落后几年的票不该让增量去拉几年数据 —— 起点被 MAX_LOOKBACK_DAYS 钳住。

    真正缺那么多的由深历史补；增量只管「最近断的这几天」。
    """
    s = db()
    _seed(s, "hk_stock", "00700.HK", latest=date(2020, 1, 1))
    s.commit()
    s.close()

    _, _, stats = _plan(db)
    floor = (TODAY - timedelta(days=m.MAX_LOOKBACK_DAYS)).isoformat()
    assert stats["start"] == floor, f"起点应被钳到 {floor}，而不是 2020"


def test_todo_sorted_by_staleness_so_batches_are_homogeneous(db):
    """todo 必须按落后程度排序 —— 这是「每批各算起点」能省流量的前提。

    实测发现的浪费：一只落后一年的票会把**全局**起点拖到 90 天前，于是只缺 9 天的
    票也要拉 90 根 bar。16k 只上是 10 倍流量。排序后同批落后程度相近，各算各的。
    """
    s = db()
    _seed(s, "hk_stock", "00001.HK", latest=date(2024, 7, 10))          # 落后一年
    _seed(s, "hk_stock", "00700.HK", latest=TODAY - timedelta(days=1))  # 只落后一天
    _seed(s, "hk_stock", "00005.HK", latest=TODAY - timedelta(days=2))
    s.commit()
    s.close()

    todo, latest, _ = _plan(db)
    assert todo == ["00700.HK", "00005.HK", "00001.HK"], "最新的在前，最陈旧的垫底"
    # 前两只组成一批时，起点只回看 2 天，不该被那只落后一年的拖累
    head_start = m.OverseasDailyUpdater._start_for(
        min(latest[s] for s in todo[:2]), TODAY)
    assert head_start == (TODAY - timedelta(days=1)).isoformat()


def test_start_for_caps_at_max_lookback():
    assert m.OverseasDailyUpdater._start_for(date(2020, 1, 1), TODAY) == \
        (TODAY - timedelta(days=m.MAX_LOOKBACK_DAYS)).isoformat()
    assert m.OverseasDailyUpdater._start_for(TODAY - timedelta(days=3), TODAY) == \
        (TODAY - timedelta(days=2)).isoformat()


def test_rejects_unknown_market():
    with pytest.raises(ValueError, match="不支持的市场"):
        m.OverseasDailyUpdater().run("a_share")


def test_hk_symbol_converted_to_yf_4digit():
    """港股库里存 5 位，yfinance 只认 4 位（**去掉首位**，不是补零）。

    忘了转 = 喂 5 位进去 yfinance 回「possibly delisted; no price data found」，
    **全军拉不到且不报错** —— 静默失败，最坏的那种。
    """
    assert m._to_yf("09988.HK", "hk_stock") == "9988.HK"
    assert m._to_yf("00700.HK", "hk_stock") == "0700.HK"
    assert m._to_yf("06909.HK", "hk_stock") == "6909.HK"
    assert m._to_yf("AAPL", "us_stock") == "AAPL", "美股原样传"


# ---------- Yahoo 互斥锁 ----------

def test_yahoo_lock_is_mutually_exclusive():
    from acquisition.markets.yf_batch import yahoo_job_lock

    with yahoo_job_lock("A") as first:
        assert first is True
        with yahoo_job_lock("B") as second:
            assert second is False, "第二个必须抢不到"
    # 出了 with 就该放开
    with yahoo_job_lock("C") as third:
        assert third is True


def test_yahoo_lock_released_on_exception():
    """锁必须在异常路径下也放开，否则一次崩溃 = 永久堵死两个 job。"""
    from acquisition.markets.yf_batch import yahoo_job_lock

    with pytest.raises(RuntimeError):
        with yahoo_job_lock("炸一个") as ok:
            assert ok
            raise RuntimeError("boom")

    with yahoo_job_lock("之后还能抢到吗") as ok:
        assert ok is True


def test_daily_update_skips_when_lock_held(db):
    """深历史正在跑时，每日增量必须**跳过**而不是硬挤进去。

    自动任务让位于手动任务：它明天还有机会，Jason 在旁边等着的那个没有。
    """
    from acquisition.markets.yf_batch import yahoo_job_lock

    holder_ready = threading.Event()
    release = threading.Event()

    def _hold():
        with yahoo_job_lock("假装深历史") as ok:
            assert ok
            holder_ready.set()
            release.wait(timeout=5)

    t = threading.Thread(target=_hold, daemon=True)
    t.start()
    holder_ready.wait(timeout=5)

    out = m.update_overseas_daily(today=TODAY)
    assert "skipped" in out, "抢不到锁必须跳过"

    release.set()
    t.join(timeout=5)


def test_deep_history_and_daily_share_the_same_lock():
    """互斥必须是**双向**的 —— 只有一边抢锁等于没锁。

    这条防的是「以后有人重构 overseas_job 时把 yahoo_job_lock 那段删了」。
    """
    import inspect

    import data_engine.deep_history.overseas_job as oj

    src = inspect.getsource(oj.OverseasDeepHistoryJob._execute)
    assert "yahoo_job_lock" in src, (
        "深历史回补必须也抢 Yahoo 互斥锁，否则每日增量单方面让路 = 没有互斥"
    )
