"""数据质量硬传导（层2）跑通真实主循环 —— 不打真实 LLM，stream_chat 脚本化。

纯函数判定在 test_hard_clamp.py。本文件守的是**接线**：
  - 工具上报的 quality 真的会进 session.turn_quality
  - 收尾时真的会追加更正 chunk
  - `_finalize`（熔断/保险丝）路径也受校验 —— 那是此前的洞，别只堵一半
  - 新 turn 真的会清空上一轮的质量
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

from agents.context import AgentSession  # noqa: E402
from agents.orchestrator import MonitorOrchestrator  # noqa: E402
from agents.registry import REGISTRY, ToolDef  # noqa: E402
from agents.tool_envelope import ToolEnvelope  # noqa: E402
from tests._fixtures import script_stream_chat  # noqa: E402

_DEGRADED = {"overall_score": 50, "level": "poor", "core_degraded": True,
             "limitations": ["daily_bars: stale"]}
_HEALTHY = {"overall_score": 100, "level": "good", "core_degraded": False,
            "limitations": []}


@pytest.fixture(autouse=True)
def _register_fake_tools():
    """注册两个假工具：一个上报降级质量，一个上报健康质量。"""
    def _degraded_tool():
        return ToolEnvelope(data={"price": 1253.0}, quality=_DEGRADED)

    def _healthy_tool():
        return ToolEnvelope(data={"price": 1253.0}, quality=_HEALTHY)

    for name, fn in (("t_degraded", _degraded_tool), ("t_healthy", _healthy_tool)):
        if not REGISTRY.has(name):      # autouse 每个测试跑一次，重名会炸
            REGISTRY.register(ToolDef(
                name=name, description="假数据工具",
                parameters={"type": "object", "properties": {}},
                fn=fn, category="test"))
    yield


def _run(monkeypatch, session, script, message="腾讯怎么样"):
    import llm_client as lc
    monkeypatch.setattr(lc, "stream_chat", script_stream_chat(script))
    orch = MonitorOrchestrator()
    return [json.loads(line) for line in
            orch.run_stream(session, message, model="fake-model")]


def _all_text(lines) -> str:
    return "".join(e.get("content", "") for e in lines if e["event"] == "chunk")


# ════════════════ 接线：质量进 session ════════════════

def test_tool_quality_lands_in_turn_quality(monkeypatch):
    session = AgentSession(session_id="tq1")
    _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_degraded", "args": {}}],
        "我非常有把握，腾讯会涨。",
    ])
    # 工具上报的降级质量确实被收集了
    assert any(q.get("core_degraded") for q in session.turn_quality)


def test_quality_not_leaked_into_llm_messages(monkeypatch):
    """🔒 质量不许塞进 messages —— llm_client 把 messages 整个透传给 API。"""
    session = AgentSession(session_id="tq2")
    _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_degraded", "args": {}}],
        "我非常有把握。",
    ])
    for m in session.messages:
        assert "_quality" not in m
        assert "quality" not in m


# ════════════════ 正常收尾路径 ════════════════

def test_correction_appended_on_degraded_plus_high_confidence(monkeypatch):
    """🔒 降级数据 + 高把握措辞 → 收尾追加系统更正。"""
    session = AgentSession(session_id="tq3")
    lines = _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_degraded", "args": {}}],
        "综合来看我非常有把握，腾讯短期会涨。",
    ])
    text = _all_text(lines)
    assert "系统更正" in text
    assert "daily_bars: stale" in text


def test_no_correction_when_humble(monkeypatch):
    """降级但回答谨慎 → 不更正。"""
    session = AgentSession(session_id="tq4")
    lines = _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_degraded", "args": {}}],
        "数据不太新，仅供参考，建议等更新后再看。",
    ])
    assert "系统更正" not in _all_text(lines)


def test_no_correction_when_healthy(monkeypatch):
    """健康数据 → 随便多自信都不更正。"""
    session = AgentSession(session_id="tq5")
    lines = _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_healthy", "args": {}}],
        "我非常有把握，腾讯会涨。",
    ])
    assert "系统更正" not in _all_text(lines)


def test_worst_quality_wins_across_tools(monkeypatch):
    """🔒 一轮里健康工具 + 降级工具 → 按最差的判，不许被冲淡。"""
    session = AgentSession(session_id="tq6")
    lines = _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_healthy", "args": {}},
         {"id": "c2", "name": "t_degraded", "args": {}}],
        "我非常有把握。",
    ])
    assert "系统更正" in _all_text(lines)


# ════════════════ 🔒 熔断/保险丝路径也受校验（此前的洞）════════════════

def test_finalize_path_also_gets_correction(monkeypatch):
    """🔒 max_rounds 保险丝路径上的最终回答也要过硬传导。

    此前 `_finalize` 完全绕过校验 —— 熔断路径上 LLM 说什么就是什么。把 MAX_ROUNDS
    压到很小，逼主循环走保险丝收尾，验证更正照样追加。
    """
    monkeypatch.setattr(MonitorOrchestrator, "MAX_ROUNDS", 2)
    session = AgentSession(session_id="tq7")
    # 每轮都调工具、从不收尾 → 撑到 MAX_ROUNDS → 走 _finalize 强制无工具收尾。
    # 那次收尾的 LLM 文本（脚本最后一个 str）声称高把握。
    lines = _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_degraded", "args": {}}],
        [{"id": "c2", "name": "t_degraded", "args": {}}],
        "我非常有把握，腾讯会涨。",     # _finalize_no_tools 的那次收尾
    ])
    assert "系统更正" in _all_text(lines)


# ════════════════ 🔒 跨 turn 隔离 ════════════════

def test_new_turn_clears_stale_quality(monkeypatch):
    """🔒 上一轮的降级不许污染这一轮。

    turn1 调降级工具，turn2 什么工具都不调、直接高把握收尾 —— turn2 不该更正，
    因为它自己没有任何降级证据。若忘了清 turn_quality，turn1 的降级会漏进 turn2。
    """
    session = AgentSession(session_id="tq8")
    _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_degraded", "args": {}}],
        "我非常有把握。",
    ], message="第一轮")

    lines2 = _run(monkeypatch, session, [
        "我非常有把握，这次数据很好。",
    ], message="第二轮")
    assert "系统更正" not in _all_text(lines2)
    assert session.turn_quality == []      # 清干净了
