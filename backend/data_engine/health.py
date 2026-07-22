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

from common.market import A_SHARE, CANONICAL_MARKETS, CRYPTO, STOCK_MARKETS
from common.market_freshness import (
    BASELINE_DAYS,
    FRESHNESS_VERSION,
    bars_behind,
    is_stale,
    reference_trading_date,
)
from common.market_time import market_today
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
    """单个市场的新鲜度。`today` 可注入，便于测试。

    ⛔ **「今天」必须按这个市场自己的时区算**，不能用服务器本地日期（此前四个市场
    共用一个 UTC+8 today）：

    - **美股**在美东。北京上午 10 点纽约还是前一晚，本地 today 比美股真实交易日超前
      一天。此前靠 `STALE_AFTER_WEEKDAYS ≥ 2` 的宽容掩盖，那是运气不是正确性。
    - **crypto** 的 `daily_quotes.date` 是 UTC 日（币安 klines openTime），未收盘那根
      还会被刻意丢弃。本地 00:00–08:00 时 UTC 还在昨天，最新 bar 只可能是「前天」→
      算出落后 2 天 → **每天凌晨误报 crypto stale**。
    """
    today = today or market_today(market)
    day_counts = _day_counts(session, market, today)
    ref, ratio = reference_trading_date(day_counts)
    # crypto 7×24 用自然日口径（周末也该有 bar）；股票用工作日口径（周末不算落后）。
    weekend_aware = market != CRYPTO
    return {
        "market": market,
        "reference_date": ref.isoformat() if ref else None,
        "coverage_ratio_of_baseline": ratio,
        "is_stale": is_stale(ref, today, weekend_aware=weekend_aware),
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
    # ⚠️ 注意这里**不给 today 兜底**：显式 None 时让每个市场各算各的（美东/上海/UTC），
    # 传了值则四个市场共用（测试注入用）。此前是先兜成本地 today 再发下去，
    # 于是美股和 crypto 都被按 UTC+8 的日期问「你今天的数据到了没」。
    by_market = {m: get_market_freshness(session, m, today) for m in CANONICAL_MARKETS}

    # 全局 is_stale / latest_date 只在**股票三市场**上聚合：crypto 是 7×24 独立链，
    # 调度器常关或币安不可达时其表为空 → is_stale 恒 True，若并入 any() 会把全局
    # 永久拉红，使新鲜度信号失效。crypto 自身陈旧仍在 by_market["crypto"] 单独体现。
    stock = {m: by_market[m] for m in STOCK_MARKETS if m in by_market}
    refs = [date.fromisoformat(v["reference_date"])
            for v in stock.values() if v["reference_date"]]
    # 木桶取短板：报最落后的那个市场。原来的全表 max 取的是最长板 —— 正好反了，
    # 这就是「一只领跑票盖住全市场陈旧」的机理。
    worst = min(refs) if refs else None

    # 顶层 today / is_weekday 是给老调用方（看板卡片）的兼容键，按 **A 股**口径给 ——
    # 这个面板本来就是 A 股视角。分市场的准确判定在 by_market 里，各自用各自的 today。
    top_today = today or market_today(A_SHARE)
    return {
        "latest_date": worst.isoformat() if worst else None,
        "today": top_today.isoformat(),
        "is_stale": any(v["is_stale"] for v in stock.values()),
        "is_weekday": top_today.weekday() < 5,
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
    today = today or market_today(market)
    ref, _ = reference_trading_date(_day_counts(session, market, today))
    row = (session.query(func.max(DailyQuote.date))
           .filter(DailyQuote.symbol == symbol).first())
    latest = _to_date(row[0]) if row and row[0] else None
    # crypto 7×24 按自然日数落后，股票按工作日 —— 与 is_stale 同口径
    return {
        "reference_date": ref.isoformat() if ref else None,
        "symbol_latest": latest.isoformat() if latest else None,
        "bars_behind": bars_behind(latest, ref, weekend_aware=(market != CRYPTO)),
    }
