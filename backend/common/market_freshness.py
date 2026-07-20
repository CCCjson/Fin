"""市场参考交易日 —— 「这个市场的数据，最新到底更到哪天了」。

**纯逻辑、DB 无关、无时间副作用**：入参是 `(date, count)` 序列和显式的 `today`，
所以可以零 fixture、零 mock、零 DB 地测。DB 查询留在 `data_engine/health.py`。
放 `common/` 是分层铁律 `engines → acquisition → common/net`：`data_engine` 与
`agents` 都要用它，只有最底层能被两边 import 而不产生反向依赖。

---

## 为什么不能用 `max(date)`

`health.get_freshness` 原本是 `session.query(func.max(DailyQuote.date))` —— 全表
最大值，不分市场、不问覆盖。后果：**任意一只票更新到今天，整个库就报「新鲜」**。
5000 只票有 4999 只烂在上周也照样 `is_stale=False`。美股还差个时区，天然容易
当那只「领跑票」。

## 为什么阈值必须是相对的（2026-07-17 真库实测定的）

天真的做法是「覆盖率 ≥ 50% 算达标」。**用真数据一试就崩**：

    a_share   活跃 5201 只，稳态每日 ~5178 只  → 覆盖 99.5%
    hk_stock  活跃 4699 只，稳态每日 ~2950 只  → 覆盖 62.8%
    us_stock  活跃 13586 只，稳态每日 ~6115 只 → 覆盖 45.0%   ← 绝对阈值 50% 会让它永远不达标

分母（`StockInfo.is_active`）本身就虚高 —— 港美股里大量标的压根没有行情（清洗过
的 hk universe 是 3978 而非 4699）。所以「达标」只能是**相对于这个市场自己的稳态
水平**，不能是跨市场的绝对数。中位数当基线，还自带自校准：universe 变了、清洗了、
新接了市场，都不用回来改常量。

实测这套口径当天就抓对了现场：港股 07-17 只有 2280 只（= 中位数的 77%，回填还在
跑）→ 判不达标 → 参考日正确地退回 07-16。
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

# 判定口径的版本戳。**改阈值/基线算法/stale 天数 = 必须 bump。**
# 同 `outcome_eval.ENGINE_VERSION` 的道理：不 bump 就是让新旧口径的结论混在一起，
# 而且你永远不知道。
FRESHNESS_VERSION = "market-freshness-v1"

# 达标线：当日覆盖数 ≥ 近期中位数 × 这个比例。
#
# 取 0.8 是在「回填半截的一天不该被当成完整交易日」与「正常交易日里停牌/新股波动
# 不该误判」之间取的。实测港股回填中的 77% 落在线下（正确判为不完整），而各市场
# 稳态日的日间波动都在 1% 以内，离 20% 的余量极远。
QUALIFIED_RATIO = 0.8

# 算中位数基线用最近几个有数据的日期。取 10 是要盖过「回填中的最近几天」——
# 只看 3 天的话，连续回填 3 天就会把基线自己拉低，达标线跟着塌，
# **越是数据烂越容易判达标**（自我实现的健康）。
BASELINE_DAYS = 10

# 参考交易日落后几个工作日算 stale。
#
# 取 2 是为了**扛住单个节假日**：项目没有交易日历（见模块末注），无法知道「今天
# 是不是交易日」。国庆/春节这种连休会误报，这是已知且刻意接受的代价 —— 宁可长假
# 里多报一次 stale，也不要漏报真的断更。
STALE_AFTER_WEEKDAYS = 2

# 加密货币（7×24 无休市）用**自然日**口径：币每天都有 bar，用工作日口径会把周末断更
# 漏报（周五停更、周一才刚好差 1 个工作日）。取 2 自然日：容忍「今天的 bar 还没落库」，
# 落后到前天才报警。crypto 没有交易日历困扰（天天开市），反而比股票口径更干净。
STALE_AFTER_DAYS = 2


def _median(values: Sequence[float]) -> float:
    """中位数。空序列返回 0（调用方据此判「压根没数据」）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def reference_trading_date(
    day_counts: Sequence[tuple[date, int]],
) -> tuple[date | None, float]:
    """这个市场的数据最新更到哪个**完整**交易日。

    Args:
        day_counts: `[(日期, 当日有 bar 的股票数), ...]`，**按日期降序**（最近的在前）。
            只需要传最近 `BASELINE_DAYS` 天左右，多传不影响正确性只是白算。

    Returns:
        `(reference_date, qualified_ratio_of_baseline)` —— `reference_date` 是最近一个
        覆盖数达标的日期；比值是它相对中位数基线的水平（供调用方展示/调试）。
        没有任何数据时返回 `(None, 0.0)`。

    为什么不直接取 `day_counts[0]`（最新那天）：那天可能正在回填、可能盘中只更了
    一半、可能只有两只票 —— 拿它当「市场最新交易日」会让所有其它票都被判成落后。
    """
    if not day_counts:
        return None, 0.0

    baseline = _median([c for _, c in day_counts[:BASELINE_DAYS]])
    if baseline <= 0:
        return None, 0.0

    threshold = baseline * QUALIFIED_RATIO
    for day, count in day_counts:          # 已按日期降序 → 第一个达标的就是最近的
        if count >= threshold:
            return day, round(count / baseline, 4)
    return None, 0.0


def weekdays_between(start: date, end: date) -> int:
    """`start` 之后到 `end` 为止有几个工作日（周一~周五），不含 `start` 本身。

    `end <= start` 返回 0。**这是个近似**：它不知道节假日，见 `STALE_AFTER_WEEKDAYS`。
    """
    if end <= start:
        return 0
    days = (end - start).days
    return sum(1 for i in range(1, days + 1)
               if (start.fromordinal(start.toordinal() + i)).weekday() < 5)


def days_between(start: date, end: date) -> int:
    """`start` 之后到 `end` 为止有几个**自然日**，不含 `start` 本身。`end <= start` 返回 0。

    给 7×24 市场（crypto）用——周末也算，不跳。
    """
    if end <= start:
        return 0
    return (end - start).days


def is_stale(reference_date: date | None, today: date, *, weekend_aware: bool = True) -> bool:
    """参考交易日是否已经落后到「该报警了」。

    `today` 是**显式入参**不是 `date.today()` —— 整个模块因此可以零 mock 地测。
    没有参考日（一天数据都没有）一律算 stale。

    Args:
        weekend_aware: True（默认，股票）按工作日数，周末不算落后；False（crypto 7×24）
            按自然日数，周末也算——币每天都该有 bar。
    """
    if reference_date is None:
        return True
    if weekend_aware:
        return weekdays_between(reference_date, today) >= STALE_AFTER_WEEKDAYS
    return days_between(reference_date, today) >= STALE_AFTER_DAYS


def bars_behind(symbol_latest: date | None, reference_date: date | None,
                *, weekend_aware: bool = True) -> int | None:
    """这只票落后市场参考交易日几个 bar。

    Args:
        weekend_aware: True（默认，股票）按工作日数；False（crypto 7×24）按自然日数。
            与 `is_stale` 同口径 —— 否则 crypto 票周五停更、参考日在周末时会被误报 fresh。

    Returns:
        `None` = 无从判断（没有参考日或这只票一根 bar 都没有）——**这不是 0**，
        「不知道落后多少」和「没落后」是两回事，别让调用方把它们混为一谈。
    """
    if reference_date is None or symbol_latest is None:
        return None
    if weekend_aware:
        return weekdays_between(symbol_latest, reference_date)
    return days_between(symbol_latest, reference_date)
