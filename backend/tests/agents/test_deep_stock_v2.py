"""
deep_stock v2 dispatch 回归测试（Phase 6）——不打真实 LLM，只验证：
- 缺 symbol 时不管 v1/v2 都走 VALIDATION_ERROR
- AGENT_DEEP_STOCK_V2 开关正确路由到 v1 / v2 分支
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.subagents.deep_stock import DeepStockSubagent  # noqa: E402


def _drain_and_get_done_result(gen):
    result = None
    for line in gen:
        ev = json.loads(line)
        if ev.get("event") == "subagent_done":
            result = ev.get("result")
    return result


def test_missing_symbol_is_validation_error():
    result = _drain_and_get_done_result(DeepStockSubagent().run({}))
    assert result["ok"] is False
    assert result["error_code"] == "validation_error"


def test_v2_is_default_and_dispatches_to_run_v2(monkeypatch):
    called = {}

    def fake_v2(self, symbol, cancel_event=None):  # run 现在会带 cancel_event 关键字传入
        called["symbol"] = symbol
        yield from iter(())

    monkeypatch.delenv("AGENT_DEEP_STOCK_V2", raising=False)
    monkeypatch.setattr(DeepStockSubagent, "_run_v2", fake_v2)
    list(DeepStockSubagent().run({"symbol": "600519.SH"}))
    assert called["symbol"] == "600519.SH"


def test_v2_off_dispatches_to_run_v1(monkeypatch):
    called = {}

    def fake_v1(self, symbol, args):
        called["symbol"] = symbol
        yield from iter(())

    monkeypatch.setenv("AGENT_DEEP_STOCK_V2", "off")
    monkeypatch.setattr(DeepStockSubagent, "_run_v1", fake_v1)
    list(DeepStockSubagent().run({"symbol": "600519.SH"}))
    assert called["symbol"] == "600519.SH"
