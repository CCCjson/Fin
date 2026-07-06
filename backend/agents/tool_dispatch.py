"""
ToolDispatcher —— 把一次 tool_call 路由到 meta 工具 / subagent / 普通工具三条路径之一。

从 orchestrator._loop 拆出来，让主循环只剩纯粹的 Reason→Act→Observe 骨架。
meta 工具（load_toolgroup）自成一体：改 session 状态、自产 tool 消息与
TOOL_RESULT，不产出通用 result dict；subagent/普通工具走统一的 run_tool 契约。

移动是纯粹的代码搬运，逻辑与移动前逐字节一致。
"""
from typing import Callable, Generator, Optional

from agents.context import AgentSession
from agents.events import EV, emit
from agents.executor import run_tool, validate_tool_args
from agents.tool_descs import done_desc
from agents.tool_groups import META_TOOL


class ToolDispatcher:
    def __init__(self, run_subagent_fn: Callable[[dict, AgentSession], Generator[str, None, dict]]):
        # run_subagent_fn 是 MonitorOrchestrator._run_subagent 的绑定方法——subagent
        # 执行仍需要 orchestrator 侧的 turn_monitor token 记账，不搬到这里以控制本次
        # 拆分的改动范围（低风险优先）。
        self._run_subagent_fn = run_subagent_fn

    @staticmethod
    def is_meta(name: str) -> bool:
        return name == META_TOOL

    @staticmethod
    def dispatch_meta(tc: dict, session: AgentSession, mon) -> Generator[str, None, None]:
        """load_toolgroup：改 session.allowed_tools，自产 tool 消息与 TOOL_RESULT。"""
        from agents import tool_groups
        groups = (tc["args"] or {}).get("groups") or []
        if session.allowed_tools is None:
            msg = "当前为全量工具模式，所有工具已可直接调用。"
        else:
            new_allowed, added, unknown = tool_groups.expand(session.allowed_tools, groups)
            session.allowed_tools = new_allowed
            parts = []
            if added:
                parts.append(f"已加载工具组 {groups}，新增可用工具：{', '.join(added)}，现在可直接调用")
            else:
                parts.append(f"工具组 {groups} 均已加载，无新增")
            if unknown:
                parts.append(f"未知组名：{unknown}（可用组：{tool_groups.group_names()}）")
            msg = "；".join(parts)
        session.messages.append({
            "role": "tool", "tool_call_id": tc["id"], "content": msg})
        if mon:
            mon.record_meta(tc)
        yield emit(EV.TOOL_RESULT, id=tc["id"], name=tc["name"], ok=True,
                   desc=done_desc(tc["name"], tc["args"], True), verdict="meta")

    def dispatch(self, tc: dict, td, session: AgentSession) -> Generator[str, None, dict]:
        """subagent 或普通工具，返回统一的 legacy dict（{"ok","summary","widget"?,"navigate"?,...}）。"""
        if td and td.is_subagent:
            if td.args_model is not None:
                # subagent 不走 run_tool，但参数校验要和普通工具共用同一份逻辑
                # （validate_tool_args），否则四个 subagent 各自手工校验、风格不一。
                # 校验失败直接短路：连 AGENT_HANDOFF 都不发，不把半个子任务派出去。
                kwargs, err = validate_tool_args(tc["name"], td.args_model, tc["args"])
                if err is not None:
                    return err
                tc["args"] = kwargs
            result = yield from self._run_subagent_fn(tc, session)
        else:
            result = run_tool(tc["name"], tc["args"])
        return result
