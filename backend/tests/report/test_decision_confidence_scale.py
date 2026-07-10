"""`DecisionLog.confidence` 的量纲是 0-100，写入口要能逮住 0-1 的误写。

真实事故：`recommend_engine` 除以 100 存成 0-1，`report_picks` 存 0-100，
同一列两个量纲。`get_decision_history` 会把 `0.67` 和 `71.4` 一起端给 MoneyBill，
跨 source 比胜率必错。
"""
import pathlib

import pytest
from loguru import logger

from decision_log import _warn_if_confidence_looks_normalized

pytestmark = pytest.mark.baseline


@pytest.fixture
def warnings():
    """loguru 不走 pytest 的 caplog，得自己挂 sink。"""
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="WARNING", format="{message}")
    yield captured
    logger.remove(sink_id)


@pytest.mark.parametrize("value", [0.65, 0.72, 1.0, 0.001])
def test_warns_on_zero_to_one_scale(value, warnings):
    _warn_if_confidence_looks_normalized("moneybill_recommend", value)
    assert any("0-1 量纲" in w for w in warnings), warnings


@pytest.mark.parametrize("value", [None, 0, 65.0, 71.4, 100.0])
def test_silent_on_valid_scale(value, warnings):
    _warn_if_confidence_looks_normalized("report_picks", value)
    assert warnings == []


def test_recommend_engine_no_longer_divides_by_100():
    """曾经是 `(composite or 0) / 100.0 or None`。"""
    src = pathlib.Path("recommend_engine/engine.py").read_text()
    assert "/ 100.0 or None" not in src
    assert 'confidence=b.get("composite") or None' in src


def test_picks_log_stores_raw_composite_score():
    src = pathlib.Path("report_engine/picks_log.py").read_text()
    assert 'confidence=rec.get("composite_score")' in src
