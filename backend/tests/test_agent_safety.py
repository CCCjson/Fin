"""
Agent 层安全修复回归测试（B1/B3/H2/H3/H4/H1）。

pytest 可跑，也可 conda run -n quant python -m pytest tests/test_agent_safety.py 直跑。
不打真实 LLM：stream_chat 全部 monkeypatch 成脚本化 fake。
"""
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 工具分组校验要求全量工具归组，测试注册的假工具会破坏校验 → 关掉分组
os.environ["AGENT_TOOL_GROUPS"] = "off"
os.environ["AGENT_TRACE"] = "off"  # 单独的 trace 测试里再开

from agents.orchestrator import MonitorOrchestrator, _sanitize_tool_args  # noqa: E402
from agents.context import AgentSession  # noqa: E402
from agents.registry import REGISTRY, ToolDef  # noqa: E402


# ──────────────────── 测试基建 ────────────────────

CALLS: list[dict] = []  # 假工具的真实执行记录


def _fake_confirm_tool(**kw):
    CALLS.append(kw)
    return {"summary": {"executed": True, **kw}}


def _ensure_tool():
    if not REGISTRY.has("t_confirm"):
        REGISTRY.register(ToolDef(
            name="t_confirm", description="需确认的假工具",
            parameters={"type": "object", "properties": {}},
            fn=_fake_confirm_tool, category="test",
            requires_confirmation=True,
        ))


def _script_stream_chat(script: list):
    """按脚本逐次返回的 fake stream_chat。script 元素 = tool_calls 列表或 None(纯文本)。"""
    it = iter(script)

    def fake(messages, model=None, tools=None, **kw):
        step = next(it)
        if step is None:
            yield {"type": "text", "content": "好的"}
            yield {"type": "done", "message": {"role": "assistant", "content": "好的"},
                   "tool_calls": [], "prompt_tokens": 10, "completion_tokens": 5}
        else:
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": tc["id"], "type": "function",
                                   "function": {"name": tc["name"],
                                                "arguments": json.dumps(tc["args"])}}
                                  for tc in step]}
            yield {"type": "done", "message": msg, "tool_calls": step,
                   "prompt_tokens": 10, "completion_tokens": 5}
    return fake


def _run(monkeypatch, session, script, message="测试"):
    import llm_client as lc
    monkeypatch.setattr(lc, "stream_chat", _script_stream_chat(script))
    orch = MonitorOrchestrator()
    return [json.loads(line) for line in
            orch.run_stream(session, message, model="fake-model")]


def _events(lines):
    return [e["event"] for e in lines]


# ──────────────────── B1: _confirmed 伪造 ────────────────────

def test_sanitize_strips_private_keys():
    assert _sanitize_tool_args({"a": 1, "_confirmed": True, "_x": 2}) == {"a": 1}
    assert _sanitize_tool_args(None) == {}
    assert _sanitize_tool_args({}) == {}


def test_llm_cannot_forge_confirmed(monkeypatch):
    """模型在 args 里伪造 _confirmed:true → 仍必须走人工确认，工具不执行。"""
    _ensure_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_b1")
    lines = _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_confirm", "args": {"symbol": "600519.SH", "_confirmed": True}}],
    ])
    assert CALLS == []  # 绝不能执行
    assert "confirm_required" in _events(lines)
    assert "await_confirm" in _events(lines)
    assert session.pending_tool_call is not None
    assert "_confirmed" not in session.pending_tool_call.args


# ──────────────────── H3: confirm 必须点名 tool_call_id ────────────────────

def test_confirm_wrong_id_rejected(monkeypatch):
    _ensure_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_h3")
    _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_confirm", "args": {"symbol": "X"}}],
    ])
    assert session.pending_tool_call is not None

    orch = MonitorOrchestrator()
    lines = [json.loads(x) for x in
             orch.resume_with_confirmation(session, True, tool_call_id="别的id",
                                           model="fake-model")]
    assert CALLS == []                                # 没执行
    assert session.pending_tool_call is not None      # pending 保留
    assert any(e["event"] == "error" for e in lines)


def test_confirm_correct_id_executes(monkeypatch):
    _ensure_tool()
    CALLS.clear()
    session = AgentSession(session_id="t_h3b")
    _run(monkeypatch, session, [
        [{"id": "c1", "name": "t_confirm", "args": {"symbol": "X"}}],
    ])
    pending_id = session.pending_tool_call.id

    import llm_client as lc
    import decision_log
    monkeypatch.setattr(lc, "stream_chat", _script_stream_chat([None]))  # 收尾纯文本
    monkeypatch.setattr(decision_log, "record_decision", lambda **kw: None)
    orch = MonitorOrchestrator()
    lines = [json.loads(x) for x in
             orch.resume_with_confirmation(session, True, tool_call_id=pending_id,
                                           model="fake-model")]
    assert len(CALLS) == 1 and CALLS[0].get("_confirmed") is True  # 服务端注入的才算数
    assert session.pending_tool_call is None
    assert any(e["event"] == "done" for e in lines)


# ──────────────────── B3: 协作取消 ────────────────────

def test_cancel_event_stops_loop(monkeypatch):
    """cancel_event 置位后，轮首直接停，不再调 LLM。"""
    session = AgentSession(session_id="t_b3")
    session.cancel_event.set()

    def boom(*a, **kw):
        raise AssertionError("断开后不应再调 LLM")
        yield  # pragma: no cover

    import llm_client as lc
    monkeypatch.setattr(lc, "stream_chat", boom)
    orch = MonitorOrchestrator()
    lines = [json.loads(x) for x in orch.run_stream(session, "hi", model="fake-model")]
    assert _events(lines) == ["start"]  # 只有 start，没有 done/error/chunk


# ──────────────────── B2: per-session 互斥 ────────────────────

def test_run_lock_mutual_exclusion():
    session = AgentSession(session_id="t_b2")
    assert session.run_lock.acquire(blocking=False)
    assert not session.run_lock.acquire(blocking=False)  # 第二个请求抢不到
    session.run_lock.release()
    assert session.run_lock.acquire(blocking=False)
    session.run_lock.release()
    assert isinstance(session.cancel_event, threading.Event)


# ──────────────────── H2: subagent 错误传播 + token 计账 ────────────────────

def _drive(gen):
    """耗尽 generator 并拿 return 值。"""
    lines = []
    try:
        while True:
            lines.append(next(gen))
    except StopIteration as s:
        return lines, s.value


def test_subagent_crash_propagates_ok_false(monkeypatch):
    import agents.subagents as subs

    class Crash:
        name = "t_sub_crash"

        def run(self, args, cancel_event=None):  # 契约新增 cancel_event，测试双桩同步
            raise RuntimeError("引擎炸了")
            yield  # pragma: no cover

    monkeypatch.setitem(subs._RUNNERS, "t_sub_crash", Crash())
    session = AgentSession(session_id="t_h2")
    orch = MonitorOrchestrator()
    _, result = _drive(orch._run_subagent(
        {"id": "c9", "name": "t_sub_crash", "args": {}}, session))
    assert result["ok"] is False
    assert "引擎炸了" in result["summary"]


def test_subagent_tokens_counted(monkeypatch):
    from agents.events import emit
    from agents.turn_monitor import TurnMonitor
    import agents.subagents as subs

    class Ok:
        name = "t_sub_ok"

        def run(self, args, cancel_event=None):  # 契约新增 cancel_event，测试双桩同步
            yield emit("subagent_done", result={
                "ok": True, "summary": "done", "widgets": [], "tokens": 1234})

    monkeypatch.setitem(subs._RUNNERS, "t_sub_ok", Ok())
    session = AgentSession(session_id="t_h2b")
    session.turn_monitor = TurnMonitor(turn_start_idx=0)
    orch = MonitorOrchestrator()
    _, result = _drive(orch._run_subagent(
        {"id": "c8", "name": "t_sub_ok", "args": {}}, session))
    assert result["ok"] is True
    assert session.turn_monitor.turn_tokens == 1234


# ──────────────────── H4: 风控键对模型只读 ────────────────────

def test_update_setting_risk_keys_readonly():
    # update_setting 已迁移到 args_model + ToolEnvelope（Phase 1），_confirmed 是
    # executor/orchestrator 层的门闩概念，不再是工具函数自己的参数 —— 走 run_tool
    # 才是这个门闩实际生效的路径，直接调函数不再接受该 kwarg。
    from agents.executor import run_tool
    from agents.tools.settings_tools import _preview_setting
    r = run_tool("update_setting", {"key": "max_position_pct", "value": "0.9"})
    assert "只读" in str(r["summary"]) or "无权" in str(r["summary"])
    assert r["business_result"] == "negative"
    p = _preview_setting({"key": "total_capital", "value": "999999"})
    assert "error" in p


# ──────────────────── H1: trace 落盘 ────────────────────

def test_trace_write(tmp_path, monkeypatch):
    import agents.trace as tr
    monkeypatch.setenv("AGENT_TRACE", "on")
    monkeypatch.setattr(tr, "_TRACE_DIR", tmp_path)
    session = AgentSession(session_id="t_trace")
    session.messages = [{"role": "system", "content": "s"},
                        {"role": "user", "content": "u"},
                        {"role": "assistant", "content": "a"}]
    session.turn_start_idx = 1
    tr.write_turn_trace(session, model="m", reason="done",
                        turn_usage={"prompt_tokens": 1, "completion_tokens": 2})
    lines = (tmp_path / "t_trace.jsonl").read_text().strip().splitlines()
    entry = json.loads(lines[0])
    assert entry["reason"] == "done"
    assert [m["role"] for m in entry["messages"]] == ["user", "assistant"]
    assert entry["usage"]["completion_tokens"] == 2


def test_trace_gc_removes_stale_files(tmp_path, monkeypatch):
    """15 天前的旧 .jsonl 被 write_turn_trace 触发的 GC 清掉；新写的那份保留。"""
    import time as _time
    import agents.trace as tr
    monkeypatch.setenv("AGENT_TRACE", "on")
    monkeypatch.setattr(tr, "_TRACE_DIR", tmp_path)
    monkeypatch.setattr(tr, "_RAW_TRACE_DIR", tmp_path / "raw")
    monkeypatch.setattr(tr, "_last_trace_cleanup_at", 0.0)  # 绕开节流

    stale = tmp_path / "mb_deadbeef0001.jsonl"
    stale.write_text("{}\n")
    old = _time.time() - 15 * 86400
    os.utime(stale, (old, old))

    session = AgentSession(session_id="t_trace_gc")
    session.messages = [{"role": "user", "content": "u"}]
    session.turn_start_idx = 0
    tr.write_turn_trace(session, model="m", reason="done")

    assert not stale.exists()                       # 旧文件被 GC
    assert (tmp_path / "t_trace_gc.jsonl").exists()  # 本次新写的保留


def test_trace_gc_respects_max_age_env(tmp_path, monkeypatch):
    """AGENT_TRACE_MAX_AGE_DAYS 调大后，15 天前的文件不再被清。"""
    import time as _time
    import agents.trace as tr
    monkeypatch.setenv("AGENT_TRACE", "on")
    monkeypatch.setenv("AGENT_TRACE_MAX_AGE_DAYS", "30")
    monkeypatch.setattr(tr, "_TRACE_DIR", tmp_path)
    monkeypatch.setattr(tr, "_RAW_TRACE_DIR", tmp_path / "raw")
    monkeypatch.setattr(tr, "_last_trace_cleanup_at", 0.0)

    keep = tmp_path / "mb_deadbeef0002.jsonl"
    keep.write_text("{}\n")
    old = _time.time() - 15 * 86400
    os.utime(keep, (old, old))

    session = AgentSession(session_id="t_trace_gc2")
    session.messages = [{"role": "user", "content": "u"}]
    session.turn_start_idx = 0
    tr.write_turn_trace(session, model="m", reason="done")

    assert keep.exists()  # 15 天 < 30 天保留期，不清


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
