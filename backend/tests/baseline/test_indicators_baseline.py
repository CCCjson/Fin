"""基线：技术指标的数值口径（单一真源 analysis_engine）。

历史上项目有两套技术指标实现（strategy/indicators.py 与 analysis_engine/），
13 域4 已把 strategy 版删除、全部调用方迁到 `AnalysisEngine.add_indicators()`，
真源唯一。本文件从「两套一致性闸门」降级为真源的**绝对数值 golden + 口径回归哨兵**：
策略消费的 19 个核心列必须存在，且关键指标末值不随重构漂移。
"""
import math

import pandas as pd
import pytest

from analysis_engine.engine import AnalysisEngine

pytestmark = pytest.mark.baseline

# 下游 strategy/strategies.py 各策略消费的 19 个核心列。真源必须恒产出这些列。
CORE_COLUMNS = [
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


def test_core_columns_present_and_superset(ohlc):
    """真源必须恒产出策略消费的 19 个核心列，且是它们的严格超集。"""
    base_cols = set(ohlc.columns)
    engine_cols = set(AnalysisEngine().add_indicators(ohlc.copy()).columns) - base_cols

    missing = set(CORE_COLUMNS) - engine_cols
    assert not missing, f"真源缺了策略要用的核心列: {sorted(missing)}"
    assert len(engine_cols) > len(CORE_COLUMNS), "真源应是核心列的超集"


def test_indicator_values_golden(ohlc):
    """关键指标末值的绝对 golden —— 防止重构悄悄改了周期或算法。

    这些数值是 strategy 版删除前的口径，真源与其在这些列 1e-9 等价，
    故删壳后此 golden 仍是「数值未变」的存续证明。
    """
    df = AnalysisEngine().add_indicators(ohlc.copy())
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
    df = AnalysisEngine().add_indicators(ohlc.copy())
    for window in (5, 10, 20, 60):
        expected = ohlc["close"].rolling(window=window).mean()
        actual = df[f"ma{window}"]
        assert (actual - expected).abs().max() < 1e-9


def test_add_indicators_does_not_mutate_input(ohlc):
    """真源 add_indicators 不得污染入参（13 域4-1 加了 df.copy() 后此契约成立）。"""
    before = ohlc.copy()
    AnalysisEngine().add_indicators(ohlc)
    pd.testing.assert_frame_equal(ohlc, before)


def test_no_lookahead_in_moving_averages(ohlc):
    """未来函数体检：截断输入后，重叠区间内的指标值必须逐点相同。

    若某个指标用了未来数据（比如 center=True 的 rolling），截断会让它变。
    """
    full = AnalysisEngine().add_indicators(ohlc.copy())
    truncated = AnalysisEngine().add_indicators(ohlc.iloc[:100].copy())

    for col in CORE_COLUMNS:
        a = full[col].iloc[:100]
        b = truncated[col]
        diff = (a - b).abs().max()
        assert diff < 1e-9, f"{col} 疑似使用了未来数据：截断后前 100 行变了（最大差异 {diff}）"
