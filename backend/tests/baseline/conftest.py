"""13.1 行为基线的公共设置。环境副作用与 tests/agents/conftest.py 保持一致。"""
import os

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import json  # noqa: E402
import pathlib  # noqa: E402

import pytest  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def report_data_weekly() -> dict:
    """ReportDataCollector.collect(report_type='weekly', period_end=2026-07-03) 的冻结快照。

    collect() 碰 DB + 出网，不能进门禁；但它下游的 ReportPlanner.plan() /
    ReportPromptBuilder.build_multi() 是纯函数。冻结输入 → 下游可离线 golden。
    """
    return json.loads((FIXTURES / "report_data_weekly.json").read_text())
