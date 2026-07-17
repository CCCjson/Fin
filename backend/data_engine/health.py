"""
数据健康度 —— 覆盖率 + 新鲜度检查。

从 api/routes/data_monitor.py 下沉：这两个函数本就是数据引擎的职责（覆盖率
转调 DailyUpdater，新鲜度直查 DailyQuote），不该让 agents/tools/monitor_tools.py
反向 import 路由层——route 和工具层现在都从这里取，消除那条反向依赖。

**判定逻辑不在这里**：这层只管查库，「什么算达标/什么算 stale」全在
`common/market_freshness.py`（纯逻辑、零 DB、可零 mock 测）。
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func

from common.market import CANONICAL_MARKETS
from common.market_freshness import (
    BASELINE_DAYS,
    FRESHNESS_VERSION,
    bars_behind,
    is_stale,
    reference_trading_date,
)
from data_engine.storage.models import DailyQuote

# 查最近多少个自然日的 (日期, 覆盖数) 喂给中位数基线。
# BASELINE_DAYS 个**交易日**要盖过周末与节假日，所以自然日窗口开得宽一些。
_LOOKBACK_DAYS = BASELINE_DAYS * 3


def get_coverage() -> dict:
    """复用 DailyUpdater.get_update_status 的口径（total/latest/coverage/last_update）。"""
    from data_engine.daily_updater import DailyUpdater
    return DailyUpdater().get_update_status()


def _to_date(value) -> "date | None":
    """DailyQuote.date 可能是 date / datetime / str，归一成 date。"""
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _day_counts(session, market: str, today: date) -> list[tuple[date, int]]:
    """`[(日期, 当日有 bar 的股票数), ...]`，按日期降序。**必须带 market 过滤** ——
    漏了它就是「日线覆盖率 300% bug」：港美股的行会混进 A 股的分子
    （实测 19,871/6,594 = 301.3%，见 daily_updater.py:1047-1051）。
    """
    rows = (session.query(DailyQuote.date, func.count(func.distinct(DailyQuote.symbol)))
            .filter(DailyQuote.market == market,
                    DailyQuote.date >= today - timedelta(days=_LOOKBACK_DAYS))
            .group_by(DailyQuote.date)
            .order_by(DailyQuote.date.desc())
            .all())
    out = []
    for raw_day, count in rows:
        day = _to_date(raw_day)
        if day is not None:
            out.append((day, int(count)))
    return out


def get_market_freshness(session, market: str, today: "date | None" = None) -> dict:
    """单个市场的新鲜度。`today` 可注入，便于测试。"""
    today = today or date.today()
    day_counts = _day_counts(session, market, today)
    ref, ratio = reference_trading_date(day_counts)
    return {
        "market": market,
        "reference_date": ref.isoformat() if ref else None,
        "coverage_ratio_of_baseline": ratio,
        "is_stale": is_stale(ref, today),
        "version": FRESHNESS_VERSION,
    }


def get_freshness(session, today: "date | None" = None) -> dict:
    """行情数据新鲜度 —— **按市场分开报**。

    ## 这个函数原本是错的（2026-07-17 重写）

    原实现是 `session.query(func.max(DailyQuote.date))` —— 全表最大值，三个 bug 叠在一起：

    1. **不分市场、不问覆盖**：任意一只票（美股还差个时区，天然是那只领跑票）更新到
       今天，整个库就报「新鲜」。5000 只票有 4999 只烂在上周也照样 `is_stale=False`。
    2. **`is_stale = latest < today` 把每个周末和节假日都判成 stale**。`is_weekday`
       算了却**没参与判定**，只是并列返回，把锅甩给调用方。
    3. **分不清「数据没到」和「今天本来就不该有数据」**。

    现在：每个市场各算各的参考交易日（最近一个覆盖数达标的日期），周末不误报。
    判定口径全在 `common/market_freshness.py`。

    Returns:
        兼容原有键（`latest_date`/`today`/`is_stale`/`is_weekday`）供老调用方用，
        `latest_date` 现在是**各市场参考交易日里最早的那个**（木桶取短板，不再是
        全表 max 那个会撒谎的最长板）；`is_stale` 任一市场陈旧即 True。
        新增 `by_market` 给需要分市场看的调用方。
    """
    today = today or date.today()
    by_market = {m: get_market_freshness(session, m, today) for m in CANONICAL_MARKETS}

    refs = [date.fromisoformat(v["reference_date"])
            for v in by_market.values() if v["reference_date"]]
    # 木桶取短板：报最落后的那个市场。原来的全表 max 取的是最长板 —— 正好反了，
    # 这就是「一只领跑票盖住全市场陈旧」的机理。
    worst = min(refs) if refs else None

    return {
        "latest_date": worst.isoformat() if worst else None,
        "today": today.isoformat(),
        "is_stale": any(v["is_stale"] for v in by_market.values()),
        "is_weekday": today.weekday() < 5,
        "by_market": by_market,
        "version": FRESHNESS_VERSION,
    }


def get_symbol_staleness(session, symbol: str, market: str,
                         today: "date | None" = None) -> dict:
    """单只票落后市场参考交易日几个工作日 —— P0-2 判 `stale` 态的原料。

    Returns:
        `{reference_date, symbol_latest, bars_behind}`。`bars_behind=None` 表示
        **无从判断**（没参考日 / 这只票一根 bar 都没有）——不是 0。
    """
    today = today or date.today()
    ref, _ = reference_trading_date(_day_counts(session, market, today))
    row = (session.query(func.max(DailyQuote.date))
           .filter(DailyQuote.symbol == symbol).first())
    latest = _to_date(row[0]) if row and row[0] else None
    return {
        "reference_date": ref.isoformat() if ref else None,
        "symbol_latest": latest.isoformat() if latest else None,
        "bars_behind": bars_behind(latest, ref),
    }
