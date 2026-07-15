"""13.1 基线：技术指标的数值口径 + 两套实现的一致性。

项目里有两套完整的技术指标实现（去重清单 ★ 项）：
  - `analysis_engine/indicators/`  —— 39 列，将作为唯一真源
  - `strategy/indicators.py`       —— 19 列，13.4-3 要改成薄封装

合并前必须证明它们算出来是同一个数，否则改薄封装会静默改变信号。
本文件就是那道闸门：共同列逐列比对 + 关键指标的绝对值 golden。
"""
import math

import pandas as pd
import pytest

from analysis_engine.engine import AnalysisEngine
from strategy.indicators import TechnicalIndicators

pytestmark = pytest.mark.baseline

# 两套实现共有的 19 列。合并后这些列的值必须一个不差。
SHARED_COLUMNS = [
    "adx", "atr", "boll_lower", "boll_mid", "boll_upper",
    "kdj_d", "kdj_j", "kdj_k", "ma10", "ma20", "ma5", "ma60",
    "macd", "macd_dea", "macd_dif", "minus_di", "plus_di",
    "rsi", "volume_ratio",
]


def _synthetic_ohlc(n: int = 120) -> pd.DataFrame:
    """确定性合成日线：趋势 + 两个不同周期的正弦，保证均线多次交叉、RSI 走完全程。

    合成而非取库内真实 K 线，是为了让基线不随数据更新漂移。
    """
    rows = []
    for i in range(n):
        base = 100.0 + i * 0.15 + 12.0 * math.sin(i / 9.0) + 4.0 * math.sin(i / 2.7)
        open_ = round(base, 2)
        close = round(base + 0.8 * math.sin(i / 3.3), 2)
        rows.append({
            "date": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": open_,
            "high": round(max(open_, close) + 0.9, 2),
            "low": round(min(open_, close) - 0.9, 2),
            "close": close,
            "volume": 1_000_000.0 + (i % 17) * 10_000.0,
        })
    return pd.DataFrame(rows)


@pytest.fixture()
def ohlc() -> pd.DataFrame:
    """函数级，不共享，避免任一用例就地改入参串到别的用例。"""
    return _synthetic_ohlc()


def test_two_indicator_implementations_agree_on_shared_columns(ohlc):
    """13.4-3 的验收闸门：把 strategy 版改成薄封装之前/之后，这条都必须绿。"""
    strategy_df = TechnicalIndicators.calculate_all_indicators(ohlc.copy())
    engine_df = AnalysisEngine().add_indicators(ohlc.copy())

    for col in SHARED_COLUMNS:
        assert col in strategy_df.columns, f"strategy 版少了 {col}"
        assert col in engine_df.columns, f"analysis_engine 版少了 {col}"
        diff = (strategy_df[col] - engine_df[col]).abs().max()
        assert diff < 1e-9, f"{col} 两套实现不一致，最大差异 {diff}"


def test_analysis_engine_is_a_superset(ohlc):
    """analysis_engine 必须覆盖 strategy 版的全部列，否则不能当真源。"""
    base_cols = set(ohlc.columns)
    strategy_cols = set(TechnicalIndicators.calculate_all_indicators(ohlc.copy()).columns) - base_cols
    engine_cols = set(AnalysisEngine().add_indicators(ohlc.copy()).columns) - base_cols

    missing = strategy_cols - engine_cols
    assert not missing, f"analysis_engine 缺了这些列，不能直接当真源: {sorted(missing)}"
    assert len(engine_cols) > len(strategy_cols)


def test_indicator_values_golden(ohlc):
    """关键指标末值的绝对 golden —— 防止重构悄悄改了周期或算法。"""
    df = TechnicalIndicators.calculate_all_indicators(ohlc.copy())
    last = df.iloc[-1]

    assert last["ma5"] == pytest.approx(119.862, abs=1e-3)
    assert last["ma20"] == pytest.approx(112.5225, abs=1e-3)
    assert last["macd_dif"] == pytest.approx(3.301783, abs=1e-5)
    assert last["macd_dea"] == pytest.approx(1.836789, abs=1e-5)
    assert last["rsi"] == pytest.approx(82.837779, abs=1e-5)
    assert last["kdj_k"] == pytest.approx(85.195113, abs=1e-5)
    assert last["boll_mid"] == pytest.approx(112.5225, abs=1e-3)


def test_ma_windows_are_what_they_claim(ohlc):
    """ma5 就得是 5 日均线。周期被人改掉是最难察觉的回归。"""
    df = TechnicalIndicators.calculate_all_indicators(ohlc.copy())
    for window in (5, 10, 20, 60):
        expected = ohlc["close"].rolling(window=window).mean()
        actual = df[f"ma{window}"]
        assert (actual - expected).abs().max() < 1e-9


def test_strategy_indicators_do_not_mutate_input(ohlc):
    """strategy 版不改调用方的 df —— 这是正确行为，合并后必须保住。"""
    before = ohlc.copy()
    TechnicalIndicators.calculate_all_indicators(ohlc)
    pd.testing.assert_frame_equal(ohlc, before)


def test_analysis_engine_indicators_do_not_mutate_input(ohlc):
    """真源 add_indicators 不得污染入参（13.4-4 加了 df.copy() 后此契约成立）。"""
    before = ohlc.copy()
    AnalysisEngine().add_indicators(ohlc)
    pd.testing.assert_frame_equal(ohlc, before)


def test_no_lookahead_in_moving_averages(ohlc):
    """未来函数体检：截断输入后，重叠区间内的指标值必须逐点相同。

    若某个指标用了未来数据（比如 center=True 的 rolling），截断会让它变。
    """
    full = TechnicalIndicators.calculate_all_indicators(ohlc.copy())
    truncated = TechnicalIndicators.calculate_all_indicators(ohlc.iloc[:100].copy())

    for col in SHARED_COLUMNS:
        a = full[col].iloc[:100]
        b = truncated[col]
        diff = (a - b).abs().max()
        assert diff < 1e-9, f"{col} 疑似使用了未来数据：截断后前 100 行变了（最大差异 {diff}）"
