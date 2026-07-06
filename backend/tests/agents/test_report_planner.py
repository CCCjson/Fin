"""
ReportPlanner 回归测试（Phase 7，纯显式建模，无并发行为）。

CHAPTER_DEPS 从 generator.py 移到 planner.py 后必须是同一个对象（不是
各自维护一份容易漂移的副本），build_multi() 的调用结果原样透传进 ReportPlan。
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from report_engine.planner import ReportPlanner, ReportPlan, CHAPTER_DEPS  # noqa: E402
from report_engine.generator import _CHAPTER_DEPS  # noqa: E402


def test_generator_reuses_same_chapter_deps_object():
    """generator.py 不能各自维护一份漂移的依赖图副本，必须是同一个对象。"""
    assert CHAPTER_DEPS is _CHAPTER_DEPS


def test_chapter_deps_shape_unchanged():
    assert CHAPTER_DEPS == {
        2: [], 3: [2], 4: [2, 3], 6: [2, 4],
        5: [2, 3, 4, 6], 7: [2, 3, 4], 8: [2, 3, 4, 5, 7],
    }


def test_plan_wraps_build_multi_result():
    fake_specs = ["spec1", "spec2"]
    with patch("report_engine.prompt_builder.ReportPromptBuilder.build_multi",
               return_value=fake_specs) as mock_build:
        plan = ReportPlanner().plan({"period_start": "2026-01-01"}, "weekly")
    assert isinstance(plan, ReportPlan)
    assert plan.specs == fake_specs
    assert plan.deps is CHAPTER_DEPS
    mock_build.assert_called_once()
