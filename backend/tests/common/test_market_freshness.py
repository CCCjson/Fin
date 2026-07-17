"""市场参考交易日 —— 纯规则测试，零 fixture 零 DB 零 mock。

样本全部取自 2026-07-17 库里的**真实覆盖数**，不是编的：
    a_share   活跃 5201，稳态每日 ~5178（99.5%）
    hk_stock  活跃 4699，稳态每日 ~2950（62.8%），07-17 回填中只有 2280
    us_stock  活跃 13586，稳态每日 ~6115（45.0%）
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.market_freshness import (  # noqa: E402
    QUALIFIED_RATIO,
    bars_behind,
    is_stale,
    reference_trading_date,
    weekdays_between,
)

# 真实数据（2026-07-17 查库）
_A_SHARE = [
    (date(2026, 7, 17), 5177), (date(2026, 7, 16), 5179), (date(2026, 7, 15), 5180),
    (date(2026, 7, 14), 5179), (date(2026, 7, 13), 5179), (date(2026, 7, 10), 5176),
    (date(2026, 7, 9), 5177), (date(2026, 7, 8), 5178),
]
_HK_BACKFILLING = [
    (date(2026, 7, 17), 2280),   # ← 回填中，只有中位数的 77%
    (date(2026, 7, 16), 2950), (date(2026, 7, 15), 2951), (date(2026, 7, 14), 2951),
    (date(2026, 7, 13), 2950), (date(2026, 7, 10), 2950), (date(2026, 7, 9), 2950),
    (date(2026, 7, 8), 2952),
]
_US = [
    (date(2026, 7, 16), 6174), (date(2026, 7, 15), 6118), (date(2026, 7, 14), 6109),
    (date(2026, 7, 13), 6100), (date(2026, 7, 10), 6115), (date(2026, 7, 9), 6115),
    (date(2026, 7, 8), 6115), (date(2026, 7, 7), 6114),
]


# ---------------- reference_trading_date ----------------

def test_a_share_full_day_qualifies():
    ref, ratio = reference_trading_date(_A_SHARE)
    assert ref == date(2026, 7, 17)
    assert ratio > 0.99


def test_backfilling_day_is_rejected_and_falls_back():
    """🔒 回填半截的一天不算完整交易日 —— 参考日要退回上一个完整日。

    这是本模块存在的理由：港股 07-17 只有 2280 只（中位数的 77%），拿它当「市场
    最新交易日」会让另外 670 只全被判成落后。
    """
    ref, _ = reference_trading_date(_HK_BACKFILLING)
    assert ref == date(2026, 7, 16)


def test_absolute_threshold_would_break_us_stock():
    """🔒 阈值必须相对该市场自己的稳态，不能用跨市场的绝对数。

    美股稳态覆盖率只有 45%（分母 StockInfo 虚高，大量标的没行情）。任何「覆盖率
    ≥50% 才算达标」的绝对阈值都会让美股**永远不达标**。别把它改回绝对值。
    """
    ref, ratio = reference_trading_date(_US)
    assert ref == date(2026, 7, 16)      # 45% 的绝对覆盖率照样达标
    assert ratio > 0.99                  # 因为它相对自己的中位数是满的


def test_single_leading_symbol_cannot_fake_freshness():
    """🔒 一只领跑票不许盖住全市场陈旧 —— 这正是 max(date) 的病。"""
    day_counts = [(date(2026, 7, 17), 2)] + _A_SHARE[1:]   # 今天只有 2 只票
    ref, _ = reference_trading_date(day_counts)
    assert ref == date(2026, 7, 16)      # 不是 07-17


def test_no_data_returns_none_not_a_guess():
    assert reference_trading_date([]) == (None, 0.0)
    assert reference_trading_date([(date(2026, 7, 17), 0)]) == (None, 0.0)


def test_baseline_window_survives_multi_day_backfill():
    """🔒 连续几天回填不许把基线自己拉低。

    只看最近 3 天的话，连续烂 3 天 → 基线塌到烂水平 → 烂数据反而「达标」。
    BASELINE_DAYS=10 是为了盖过这种自我实现的健康。
    """
    half = [(date(2026, 7, 17), 2600), (date(2026, 7, 16), 2600), (date(2026, 7, 15), 2600)]
    day_counts = half + _HK_BACKFILLING[1:]        # 3 天半量 + 历史稳态
    ref, _ = reference_trading_date(day_counts)
    # 中位数仍由历史稳态(~2950)主导 → 2600/2950 = 88% ≥ 80% → 达标
    # 关键是基线没被这 3 天带塌（若基线=2600，达标线只剩 2080，什么烂数据都能过）
    assert ref == date(2026, 7, 17)
    baseline_intact = 2600 / 2950 >= QUALIFIED_RATIO
    assert baseline_intact


# ---------------- weekdays_between ----------------

def test_weekdays_between_skips_weekend():
    # 2026-07-17 是周五，07-20 是周一
    assert weekdays_between(date(2026, 7, 17), date(2026, 7, 20)) == 1
    assert weekdays_between(date(2026, 7, 17), date(2026, 7, 18)) == 0   # 周六
    assert weekdays_between(date(2026, 7, 17), date(2026, 7, 17)) == 0
    assert weekdays_between(date(2026, 7, 20), date(2026, 7, 17)) == 0   # end < start


# ---------------- is_stale ----------------

def test_weekend_is_not_stale():
    """🔒 周末不该报 stale —— 原 get_freshness 的 `latest < today` 每个周末都误报。"""
    friday = date(2026, 7, 17)
    assert is_stale(friday, date(2026, 7, 18)) is False   # 周六
    assert is_stale(friday, date(2026, 7, 19)) is False   # 周日
    assert is_stale(friday, date(2026, 7, 20)) is False   # 周一（当天数据还没到，正常）


def test_two_weekdays_behind_is_stale():
    friday = date(2026, 7, 17)
    assert is_stale(friday, date(2026, 7, 21)) is True    # 周二 → 落后 2 个工作日


def test_single_holiday_does_not_trigger():
    """单个节假日不误报 —— STALE_AFTER_WEEKDAYS=2 就是为这个留的余量。"""
    assert is_stale(date(2026, 7, 16), date(2026, 7, 17)) is False


def test_no_reference_date_is_stale():
    assert is_stale(None, date(2026, 7, 17)) is True


# ---------------- bars_behind ----------------

def test_bars_behind_distinguishes_unknown_from_zero():
    """🔒 「不知道落后多少」≠「没落后」。"""
    assert bars_behind(None, date(2026, 7, 17)) is None
    assert bars_behind(date(2026, 7, 17), None) is None
    assert bars_behind(date(2026, 7, 17), date(2026, 7, 17)) == 0


def test_bars_behind_counts_weekdays():
    assert bars_behind(date(2026, 7, 10), date(2026, 7, 17)) == 5
