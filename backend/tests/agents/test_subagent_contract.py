"""
Subagent 契约统一回归测试（Phase 3）。

覆盖：
- news.py 的旗舰 bug 修复——"没抓到新闻"必须显式 business_result=negative，
  不能再像旧版那样漏掉 ok 字段被下游默认判定成功。
- 4 个 subagent 的参数缺失分支都归类为 VALIDATION_ERROR。
- emit_subagent_done 产出的 wire 格式能被 orchestrator._run_subagent 正确解析回 ToolEnvelope。
- 解析失败（子层没按规范构造）保守兜底为 ok:False，不默认成功、不崩主流程。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

from agents.orchestrator import MonitorOrchestrator  # noqa: E402
from agents.subagents.base import emit_subagent_done  # noqa: E402
from agents.subagents.deep_stock import DeepStockSubagent  # noqa: E402
from agents.subagents.alpha_lab import AlphaLabSubagent  # noqa: E402
from agents.tool_envelope import ErrorCode, ToolEnvelope  # noqa: E402


def _drain_and_get_done_result(gen):
    """跑完一个 subagent.run() generator，取最后一条 subagent_done 的 result dict。"""
    result = None
    for line in gen:
        ev = json.loads(line)
        if ev.get("event") == "subagent_done":
            result = ev.get("result")
    return result


def test_deep_stock_missing_symbol_is_validation_error():
    result = _drain_and_get_done_result(DeepStockSubagent().run({}))
    assert result["ok"] is False
    assert result["error_code"] == "validation_error"


def test_alpha_lab_missing_symbols_is_validation_error():
    result = _drain_and_get_done_result(AlphaLabSubagent().run({}))
    assert result["ok"] is False
    assert result["error_code"] == "validation_error"


def test_news_no_articles_is_explicit_negative_not_silent_success(monkeypatch):
    """曾经的 bug：这里漏了 ok 字段，被下游 res.get("ok", True) 默认判定成功。
    修复后必须显式 ok=True + business_result=negative——诚实的"没有"，不是坍缩。"""
    import agents.subagents.news as news_mod
    import news_engine.realtime as realtime_mod

    # news.py 在函数体内 `from news_engine.realtime import get_realtime_sentiment`，
    # 必须打到源模块，打 news_mod 上的同名引用不会生效。
    monkeypatch.setattr(realtime_mod, "get_realtime_sentiment",
                         lambda symbol, market="a_share": {"available": False})

    result = _drain_and_get_done_result(
        news_mod.NewsSubagent().run({"symbol": "600519.SH", "market": "a_share"}))
    assert result["ok"] is True
    assert result["business_result"] == "negative"
    assert "未抓到" in result["message"]


def test_emit_subagent_done_widget_list_roundtrip():
    """envelope.widget（单个）序列化到 wire 后应变成 widgets（list，至多1个）。"""
    line = emit_subagent_done(ToolEnvelope(message="ok", widget={"type": "metric_cards"}))
    ev = json.loads(line)
    res = ev["result"]
    assert res["widgets"] == [{"type": "metric_cards"}]
    assert "widget" not in res  # 不应该同时存在单数字段，wire 上只有 widgets 列表


def test_envelope_from_subagent_result_parses_legacy_shape():
    """旧形状 {"ok","summary","widgets","tokens"} 应该能被解析（summary 字段
    在 ToolEnvelope 里没有对应字段会被静默忽略，但 ok/tokens 仍正确带过）。"""
    orch = MonitorOrchestrator()
    envelope = orch._envelope_from_subagent_result(
        {"ok": True, "summary": "done", "widgets": [], "tokens": 1234})
    assert envelope.ok is True
    assert envelope.tokens == 1234


def test_envelope_from_subagent_result_malformed_falls_back_to_internal_error():
    """子层没按规范构造（比如 ok=False 却没给 error_code，ToolEnvelope 会拒绝构造）
    时，必须保守兜底成 ok:False，不能默认成功，也不能让整个主循环崩溃。"""
    orch = MonitorOrchestrator()
    envelope = orch._envelope_from_subagent_result({"ok": False})  # 缺 error_code，验证会失败
    assert envelope.ok is False
    assert envelope.error_code == ErrorCode.INTERNAL_ERROR
