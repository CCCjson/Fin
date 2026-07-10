"""13.2 report 拆解的公共设置。环境副作用与 tests/baseline/conftest.py 保持一致。"""
import os

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import json  # noqa: E402
import pathlib  # noqa: E402

import pytest  # noqa: E402

BASELINE_FIXTURES = pathlib.Path(__file__).parent.parent / "baseline" / "fixtures"


@pytest.fixture(scope="session")
def report_data_weekly() -> dict:
    """复用 13.1 冻结的 collect() 快照（period_end=2026-07-03）。"""
    return json.loads((BASELINE_FIXTURES / "report_data_weekly.json").read_text())


@pytest.fixture(autouse=True)
def _clear_common_cache():
    """collect_common 的进程内 TTL 缓存是模块级的，测试之间必须清干净。"""
    from report_engine import data_collector as dc
    dc._common_cache.clear()
    yield
    dc._common_cache.clear()
