"""
ConfirmationGate 状态机直测（Phase 1 安全网）——此前只被 test_agent_safety.py
通过 orchestrator.resume_with_confirmation 间接覆盖，这里直接测 ConfirmationGate
类本身的 intercept/resume，隔离 orchestrator 编排噪音。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import json  # noqa: E402

from agents.confirm_gate import ConfirmationGate  # noqa: E402
from agents.context import AgentSession, PendingToolCall  # noqa: E402
from agents.registry import REGISTRY, ToolDef  # noqa: E402


CALLS: list[dict] = []


def _fake_confirm_fn(**kw):
    CALLS.append(kw)
    return {"summary": {"executed": True, **kw}}


def _ensure_tool(preview_fn=None):
    name = "t_gate_confirm"
    if REGISTRY.has(name):
        td = REGISTRY.get(name)
        td.preview_fn = preview_fn  # 每次调用按当前用例覆写，registry 是跨用例单例
        return td
    td = ToolDef(
        name=name, description="需确认的假工具",
        parameters={"type": "object", "properties": {}},
        fn=_fake_confirm_fn, category="test",
        requires_confirmation=True, preview_fn=preview_fn,
    )
    REGISTRY.register(td)
    return td


def _drain(gen):
    lines = []
    try:
        while True:
            lines.append(next(gen))
    except StopIteration as s:
        return lines, s.value


def _events(lines):
    return [json.loads(x)["event"] for x in lines]


# ──────────────────── intercept ────────────────────

def test_intercept_returns_none_when_tool_not_requires_confirmation():
    td = ToolDef(name="t_gate_plain", description="不需确认", fn=lambda **kw: {},
                 category="test", parameters={"type": "object", "properties": {}})
    if not REGISTRY.has("t_gate_plain"):
        REGISTRY.register(td)
    session = AgentSession(session_id="t_gate_plain_sess")
    tc = {"id": "c1", "name": "t_gate_plain", "args": {}}
    gate = ConfirmationGate()
    assert gate.intercept(tc, td, session) is None


def test_intercept_returns_none_when_already_confirmed():
    td = _ensure_tool()
    session = AgentSession(session_id="t_gate_already")
    tc = {"id": "c2", "name": "t_gate_confirm", "args": {"_confirmed": True}}
    gate = ConfirmationGate()
    assert gate.intercept(tc, td, session) is None


def test_intercept_blocks_and_writes_pending_with_preview():
    td = _ensure_tool(preview_fn=lambda args: {"预览": "详情"})
    session = AgentSession(session_id="t_gate_block")
    tc = {"id": "c3", "name": "t_gate_confirm", "args": {"symbol": "600519.SH"}}
    gate = ConfirmationGate()
    gen = gate.intercept(tc, td, session)
    assert gen is not None
    lines, _ = _drain(gen)
    assert _events(lines) == ["confirm_required", "await_confirm"]
    confirm_ev = json.loads(lines[0])
    assert confirm_ev["preview"] == {"预览": "详情"}
    assert session.pending_tool_call == PendingToolCall("c3", "t_gate_confirm", {"symbol": "600519.SH"})


def test_intercept_preview_fn_exception_is_caught_and_reported():
    def boom(args):
        raise RuntimeError("预览计算炸了")
    td = _ensure_tool(preview_fn=boom)
    session = AgentSession(session_id="t_gate_preview_boom")
    tc = {"id": "c4", "name": "t_gate_confirm", "args": {}}
    gate = ConfirmationGate()
    lines, _ = _drain(gate.intercept(tc, td, session))
    confirm_ev = json.loads(lines[0])
    assert "error" in confirm_ev["preview"]
    assert "预览计算炸了" in confirm_ev["preview"]["error"]
    # preview 失败不应该阻止拦截本身——pending 仍要写
    assert session.pending_tool_call is not None


# ──────────────────── resume：无 pending ────────────────────

def test_resume_no_pending_returns_false_and_emits_error():
    session = AgentSession(session_id="t_gate_no_pending")
    gate = ConfirmationGate()
    lines, should_continue = _drain(
        gate.resume(session, True, tool_call_id=None, model="fake-model"))
    assert should_continue is False
    assert _events(lines) == ["error"]


# ──────────────────── resume：tool_call_id 不匹配 ────────────────────

def test_resume_wrong_tool_call_id_rejected_and_pending_preserved():
    session = AgentSession(session_id="t_gate_wrong_id")
    session.pending_tool_call = PendingToolCall("real_id", "t_gate_confirm", {})
    gate = ConfirmationGate()
    lines, should_continue = _drain(
        gate.resume(session, True, tool_call_id="别的id", model="fake-model"))
    assert should_continue is False
    assert _events(lines) == ["error"]
    assert session.pending_tool_call is not None
    assert session.pending_tool_call.id == "real_id"


def test_resume_empty_or_none_tool_call_id_rejected():
    """H3 补洞：空串/None 的 tool_call_id 也必须被拒（不能靠短路当成"不点名也放行"），
    pending 保留。此前 `if tool_call_id and ...` 会跳过校验，等于确认门被绕过。"""
    for bad_id in ("", None):
        session = AgentSession(session_id=f"t_gate_empty_id_{bad_id!r}")
        session.pending_tool_call = PendingToolCall("real_id", "t_gate_confirm", {})
        gate = ConfirmationGate()
        lines, should_continue = _drain(
            gate.resume(session, True, tool_call_id=bad_id, model="fake-model"))
        assert should_continue is False
        assert _events(lines) == ["error"]
        assert session.pending_tool_call is not None
        assert session.pending_tool_call.id == "real_id"


# ──────────────────── resume：批准 → 执行 + _confirmed 注入 ────────────────────

def test_resume_approved_executes_with_confirmed_flag_injected(monkeypatch):
    import decision_log
    monkeypatch.setattr(decision_log, "record_decision", lambda **kw: None)
    _ensure_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_gate_approve")
    session.pending_tool_call = PendingToolCall("c5", "t_gate_confirm", {"symbol": "X"})
    gate = ConfirmationGate()
    lines, should_continue = _drain(
        gate.resume(session, True, tool_call_id="c5", model="fake-model"))
    assert should_continue is True
    assert len(CALLS) == 1
    assert CALLS[0].get("_confirmed") is True
    assert CALLS[0].get("symbol") == "X"
    assert session.pending_tool_call is None
    assert _events(lines) == ["start", "tool_result"]
    assert session.messages[-1]["role"] == "tool"
    assert session.messages[-1]["tool_call_id"] == "c5"


def test_resume_approved_decision_log_failure_does_not_break_flow(monkeypatch):
    """决策留痕(record_decision)炸了不应该影响主流程——留痕不可影响对话（既有原则）。"""
    import decision_log

    def boom(**kw):
        raise RuntimeError("留痕挂了")
    monkeypatch.setattr(decision_log, "record_decision", boom)
    _ensure_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_gate_decision_boom")
    session.pending_tool_call = PendingToolCall("c6", "t_gate_confirm", {"symbol": "Y"})
    gate = ConfirmationGate()
    lines, should_continue = _drain(
        gate.resume(session, True, tool_call_id="c6", model="fake-model"))
    assert should_continue is True
    assert len(CALLS) == 1


# ──────────────────── resume：取消 ────────────────────

def test_resume_rejected_writes_cancel_message_without_executing():
    _ensure_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_gate_reject")
    session.pending_tool_call = PendingToolCall("c7", "t_gate_confirm", {"symbol": "Z"})
    gate = ConfirmationGate()
    lines, should_continue = _drain(
        gate.resume(session, False, tool_call_id="c7", model="fake-model"))
    assert should_continue is True
    assert CALLS == []  # 绝不能执行
    assert session.pending_tool_call is None
    events = _events(lines)
    assert "tool_result" in events
    tool_result_ev = json.loads(lines[events.index("tool_result")])
    assert tool_result_ev["ok"] is False
    assert session.messages[-1]["role"] == "tool"
    assert "取消" in session.messages[-1]["content"]
