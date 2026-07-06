"""
data_tools.py::search_stocks 候选窗口回归测试（code-review 发现：曾被意外收窄到
top 5，模糊关键字命中 6-10 个候选时会漏掉正确的那只；锁死回 top 10）。
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.tools.data_tools import search_stocks  # noqa: E402


def test_search_stocks_returns_top_10_not_5():
    fake_results = [{"symbol": f"60000{i}.SH", "name": f"股票{i}"} for i in range(12)]
    with patch("agents.tools.data_tools._get_engine") as mock_engine:
        mock_engine.return_value.search_stocks.return_value = fake_results
        r = search_stocks(keyword="股票", market="a_share")
    assert r["summary"]["count"] == 12
    assert len(r["summary"]["matches"]) == 10
    assert r["summary"]["matches"] == fake_results[:10]
