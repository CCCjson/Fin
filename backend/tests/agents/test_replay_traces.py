"""
真实历史 trace 回放回归测试（Harness 收尾）。

把 agents/trace.py 已经在做的"每 turn 完整消息切片存档"直接当回归 fixture 用：
不打真实 LLM，只读 fixtures/traces/*.jsonl（从 data/agent_traces/ 精选的真实
对话存档）验证两件事：
1. 历史里模型真实发过的每个 tool_call 参数，放到今天的 args_model 下仍应校验
   通过——防止悄悄改了某工具的必填字段/约束导致历史行为静默失效没人发现。
2. 任何 requires_confirmation 工具（如 place_order）只要在某个 session 里出现
   过 tool_call，这个 session 的 trace 里必须有一条 reason=await_confirm 记录——
   确认门被真实走过，不是被绕过。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "on")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import agents  # noqa: E402 — 触发全量工具/subagent 注册
from agents.registry import REGISTRY  # noqa: E402

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "traces"


def _load_trace(name: str) -> list[dict]:
    path = _FIXTURES_DIR / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _all_fixture_names() -> list[str]:
    return sorted(p.name for p in _FIXTURES_DIR.glob("*.jsonl"))


def _iter_tool_calls(turn: dict):
    for m in turn.get("messages", []):
        if m.get("role") != "assistant":
            continue
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            yield name, args


@pytest.mark.parametrize("fixture_name", _all_fixture_names())
def test_recorded_tool_calls_still_validate(fixture_name):
    trace = _load_trace(fixture_name)
    checked = 0
    for turn in trace:
        for name, args in _iter_tool_calls(turn):
            if not REGISTRY.has(name):
                continue  # 工具已下线，不是这个测试要盯的事
            td = REGISTRY.get(name)
            if td.args_model is None:
                continue  # 未迁移到 args_model 的工具（meta/subagent），不校验
            try:
                td.args_model.model_validate(args)
            except ValidationError as e:
                pytest.fail(
                    f"{fixture_name} 里历史真实调用的 {name}({args}) 在今天的 "
                    f"args_model 下校验失败，工具参数可能被意外改动破坏了向后兼容：{e}")
            checked += 1
    assert checked >= 0  # 允许 0（比如整段 trace 没有工具调用），但函数必须跑通不炸


def test_confirmation_never_bypassed_in_traces():
    for fixture_name in _all_fixture_names():
        trace = _load_trace(fixture_name)
        confirm_tools_called = set()
        saw_await_confirm = False
        for turn in trace:
            if turn.get("reason") == "await_confirm":
                saw_await_confirm = True
            for name, _args in _iter_tool_calls(turn):
                if REGISTRY.has(name) and REGISTRY.get(name).requires_confirmation:
                    confirm_tools_called.add(name)
        if confirm_tools_called:
            assert saw_await_confirm, (
                f"{fixture_name} 里调用了需要确认的工具 {confirm_tools_called}，"
                "但整个 trace 里没有 await_confirm 记录——确认门可能被绕过了。")
