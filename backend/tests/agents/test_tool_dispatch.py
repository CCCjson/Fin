"""
ToolDispatcher 三路由回归测试（Phase 1 安全网）——meta 工具(load_toolgroup) /
subagent / 普通工具，agents/tool_dispatch.py 全仓无测试覆盖。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import json  # noqa: E402

from agents.context import AgentSession  # noqa: E402
from agents.registry import REGISTRY, ToolDef  # noqa: E402
from agents.tool_dispatch import ToolDispatcher  # noqa: E402


def _drain(gen):
    """跑完一个 generator 并收集所有 yield 出的行 + return 值。"""
    lines = []
    try:
        while True:
            lines.append(next(gen))
    except StopIteration as s:
        return lines, s.value


def _events(lines):
    return [json.loads(x)["event"] for x in lines]


def _ensure_plain_tool():
    if not REGISTRY.has("t_dispatch_plain"):
        REGISTRY.register(ToolDef(
            name="t_dispatch_plain", description="普通工具",
            parameters={"type": "object", "properties": {}},
            fn=lambda **kw: {"summary": "普通工具结果"}, category="test",
        ))


# ──────────────────── is_meta ────────────────────

def test_is_meta_recognizes_load_toolgroup():
    assert ToolDispatcher.is_meta("load_toolgroup") is True
    assert ToolDispatcher.is_meta("get_daily_data") is False


# ──────────────────── dispatch_meta：allowed_tools=None（全量模式）────────────────────

def test_dispatch_meta_full_mode_reports_all_tools_visible():
    session = AgentSession(session_id="t_meta_full")
    session.allowed_tools = None
    tc = {"id": "c1", "name": "load_toolgroup", "args": {"groups": ["news"]}}
    lines, _ = _drain(ToolDispatcher.dispatch_meta(tc, session, mon=None))
    assert _events(lines) == ["tool_result"]
    # 全量模式下 session.allowed_tools 应保持 None（不该被 meta 工具误改窄）
    assert session.allowed_tools is None
    assert session.messages[-1]["role"] == "tool"
    assert session.messages[-1]["tool_call_id"] == "c1"
    assert "全量" in session.messages[-1]["content"]


# ──────────────────── dispatch_meta：allowed_tools 受限模式 ────────────────────

def test_dispatch_meta_expands_allowed_tools_and_reports_unknown_group():
    session = AgentSession(session_id="t_meta_expand")
    session.allowed_tools = {"load_toolgroup"}
    tc = {"id": "c2", "name": "load_toolgroup", "args": {"groups": ["不存在的组"]}}
    lines, _ = _drain(ToolDispatcher.dispatch_meta(tc, session, mon=None))
    assert _events(lines) == ["tool_result"]
    assert "未知组名" in session.messages[-1]["content"]
    # allowed_tools 不应该因为未知组名报错而崩溃，保持不变
    assert session.allowed_tools == {"load_toolgroup"}


def test_dispatch_meta_records_meta_on_monitor_when_present():
    """mon 不为 None 时，meta 调用要登记进 TurnMonitor 轨迹（不计入坏 streak）。"""
    from agents.turn_monitor import TurnMonitor

    session = AgentSession(session_id="t_meta_mon")
    session.allowed_tools = None
    mon = TurnMonitor(turn_start_idx=0)
    tc = {"id": "c3", "name": "load_toolgroup", "args": {"groups": []}}
    _drain(ToolDispatcher.dispatch_meta(tc, session, mon))
    assert len(mon.records) == 1
    assert mon.records[0].verdict == "meta"
    assert mon.consecutive_bad == 0  # meta 不计入坏调用 streak


# ──────────────────── dispatch：普通工具 → run_tool ────────────────────

def test_dispatch_routes_plain_tool_to_run_tool():
    _ensure_plain_tool()
    session = AgentSession(session_id="t_dispatch_plain_sess")
    dispatcher = ToolDispatcher(run_subagent_fn=lambda tc, sess: (_ for _ in ()).throw(
        AssertionError("普通工具不应该走 subagent 分支")))
    tc = {"id": "c4", "name": "t_dispatch_plain", "args": {}}
    td = REGISTRY.get("t_dispatch_plain")
    result = _drain(dispatcher.dispatch(tc, td, session))[1]
    assert result["ok"] is True
    assert result["summary"] == "普通工具结果"


def test_dispatch_routes_plain_tool_when_td_is_none():
    """td=None（未注册工具，理论上不该发生但要防御）时仍走 run_tool，
    由 run_tool 自身产出"未注册"错误，而不是在 dispatch 层就崩溃。"""
    session = AgentSession(session_id="t_dispatch_none_td")
    dispatcher = ToolDispatcher(run_subagent_fn=lambda tc, sess: (_ for _ in ()).throw(
        AssertionError("不应该走 subagent 分支")))
    tc = {"id": "c5", "name": "t_dispatch_unregistered", "args": {}}
    result = _drain(dispatcher.dispatch(tc, None, session))[1]
    assert result["ok"] is False


# ──────────────────── dispatch：subagent → run_subagent_fn ────────────────────

def test_dispatch_routes_subagent_to_run_subagent_fn():
    if not REGISTRY.has("t_dispatch_subagent"):
        REGISTRY.register(ToolDef(
            name="t_dispatch_subagent", description="子代理占位",
            parameters={"type": "object", "properties": {}},
            fn=lambda **kw: {}, category="test", is_subagent=True,
        ))
    session = AgentSession(session_id="t_dispatch_sub_sess")
    calls = []

    def fake_run_subagent(tc, sess):
        calls.append((tc["name"], sess.session_id))
        yield json.dumps({"event": "agent_handoff", "agent": tc["name"]}) + "\n"
        return {"ok": True, "summary": "子代理结果"}

    dispatcher = ToolDispatcher(run_subagent_fn=fake_run_subagent)
    tc = {"id": "c6", "name": "t_dispatch_subagent", "args": {}}
    td = REGISTRY.get("t_dispatch_subagent")
    lines, result = _drain(dispatcher.dispatch(tc, td, session))
    assert calls == [("t_dispatch_subagent", "t_dispatch_sub_sess")]
    assert _events(lines) == ["agent_handoff"]
    assert result["ok"] is True
    assert result["summary"] == "子代理结果"
