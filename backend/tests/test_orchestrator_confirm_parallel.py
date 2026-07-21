"""确认门 + 并行工具调用：早退时裁掉未应答的 tool_calls，保持对话历史合法。

复现 Jason 的坑：让 MoneyBill「自己调参数试多组策略」→ 一轮里并排发多个需确认的
compile 调用 → 旧代码在第一个确认项就 return，剩下的 tool_call 没人应答 →
下次调 LLM 报 400 tool_call_ids did not have response messages。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

from agents.context import AgentSession  # noqa: E402
from agents.orchestrator import MonitorOrchestrator, _trim_unanswered_tool_calls  # noqa: E402
from agents.registry import REGISTRY, ToolDef  # noqa: E402

CALLS: list[dict] = []


def _ensure_confirm_tool():
    name = "t_par_confirm"
    if not REGISTRY.has(name):
        REGISTRY.register(ToolDef(
            name=name, description="需确认的假工具",
            parameters={"type": "object", "properties": {}},
            fn=lambda **kw: CALLS.append(kw) or {"summary": {"executed": True}},
            category="test", requires_confirmation=True))
    return name


def _script_stream_chat(script):
    it = iter(script)

    def fake(messages, model=None, tools=None, **kw):
        step = next(it)
        if step is None:
            yield {"type": "done", "message": {"role": "assistant", "content": "好的"},
                   "tool_calls": [], "prompt_tokens": 5, "completion_tokens": 5}
        else:
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": tc["id"], "type": "function",
                                   "function": {"name": tc["name"],
                                                "arguments": json.dumps(tc["args"])}}
                                  for tc in step]}
            yield {"type": "done", "message": msg, "tool_calls": step,
                   "prompt_tokens": 5, "completion_tokens": 5}
    return fake


def _run(monkeypatch, session, script):
    import llm_client as lc
    monkeypatch.setattr(lc, "stream_chat", _script_stream_chat(script))
    orch = MonitorOrchestrator()
    return [json.loads(x) for x in orch.run_stream(session, "试多组", model="fake-model")]


def _last_assistant_tool_ids(session):
    for m in reversed(session.messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return [tc["id"] for tc in m["tool_calls"]]
    return []


def _assert_history_valid(session):
    """契约：最后一个带 tool_calls 的 assistant 消息里，每个 tool_call 要么已有 tool 回复，
    要么正是当前 pending（会在 resume 时补回复）。"""
    ids = _last_assistant_tool_ids(session)
    answered = {m["tool_call_id"] for m in session.messages if m.get("role") == "tool"}
    pending = session.pending_tool_call.id if session.pending_tool_call else None
    for cid in ids:
        assert cid in answered or cid == pending, f"tool_call {cid} 没有对应回复也不是 pending → 非法历史"


# ──────────────── 单元：裁剪函数 ────────────────

def test_trim_keeps_first_n():
    msg = {"role": "assistant", "tool_calls": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}
    _trim_unanswered_tool_calls(msg, 1)
    assert [t["id"] for t in msg["tool_calls"]] == ["A"]


def test_trim_guards_none_and_missing():
    _trim_unanswered_tool_calls(None, 1)                 # 不炸
    m = {"role": "assistant"}
    _trim_unanswered_tool_calls(m, 0)                    # 无 tool_calls 键，不炸
    m2 = {"tool_calls": [{"id": "A"}]}
    _trim_unanswered_tool_calls(m2, 5)                   # keep 超长度 → 不变
    assert [t["id"] for t in m2["tool_calls"]] == ["A"]


# ──────────────── 集成：并行确认调用 ────────────────

def test_parallel_confirm_trims_to_single_pending(monkeypatch):
    _ensure_confirm_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_par_1")
    _run(monkeypatch, session, [
        [{"id": "A", "name": "t_par_confirm", "args": {}},
         {"id": "B", "name": "t_par_confirm", "args": {}}],
    ])
    # 第一个 A 触发确认 → 早退；assistant.tool_calls 应被裁到只剩 A（B 被丢弃）
    assert _last_assistant_tool_ids(session) == ["A"]
    assert session.pending_tool_call is not None and session.pending_tool_call.id == "A"
    assert CALLS == []                       # 都没执行（等确认）
    _assert_history_valid(session)           # 历史合法：无「有 tool_call 无回复」的悬空


def test_resume_after_parallel_confirm_history_valid(monkeypatch):
    import decision_log
    monkeypatch.setattr(decision_log, "record_decision", lambda **kw: None)
    _ensure_confirm_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_par_2")
    _run(monkeypatch, session, [
        [{"id": "A", "name": "t_par_confirm", "args": {}},
         {"id": "B", "name": "t_par_confirm", "args": {}}],
    ])
    # 确认 A：resume 后续跑（收尾纯文本），A 应得到 tool 回复，历史每个 tool_call 都答齐
    import llm_client as lc
    monkeypatch.setattr(lc, "stream_chat", _script_stream_chat([None]))
    orch = MonitorOrchestrator()
    list(orch.resume_with_confirmation(session, True, tool_call_id="A", model="fake-model"))
    answered = {m["tool_call_id"] for m in session.messages if m.get("role") == "tool"}
    assert "A" in answered
    assert len(CALLS) == 1                    # A 执行了一次
    assert session.pending_tool_call is None
