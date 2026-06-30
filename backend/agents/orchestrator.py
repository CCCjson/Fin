"""
MonitorOrchestrator —— MoneyBill 的编排循环（function-calling agent loop）。

同步 Generator，产 NDJSON 行；按 api/routes/advisor.py 的 thread/queue 模式在 worker
线程里被 drain。无 subagent/widget/确认 时退化为普通对话。
"""
import json
from typing import Generator, Optional

from loguru import logger

from llm_config import get_best_model
from agents.events import EV, emit
from agents.registry import REGISTRY
from agents.executor import run_tool
from agents.context import AgentSession, PendingToolCall
from agents.skills_loader import load_monitor_system_prompt


def _format_page_context(pc: dict) -> str:
    """把前端传来的页面上下文拼成一段提示，实现「针对当前页面提问」。"""
    page = pc.get("page") or "未知页面"
    entities = pc.get("entities") or {}
    parts = [f"[当前 Jason 正在查看：{page}页]"]
    if entities:
        kv = "，".join(f"{k}={v}" for k, v in entities.items())
        parts.append(f"[页面对象：{kv}]")
    parts.append("（如果 Jason 的问题指代「这只股票/这个回测/当前」，优先理解为上述页面对象。）")
    return " ".join(parts)


def _tool_msg_content(summary) -> str:
    return summary if isinstance(summary, str) else json.dumps(
        summary, ensure_ascii=False, default=str)


class MonitorOrchestrator:
    MAX_ROUNDS = 8

    def run_stream(
        self,
        session: AgentSession,
        user_message: str,
        *,
        model: Optional[str] = None,
        page_context: Optional[dict] = None,
    ) -> Generator[str, None, None]:
        model = model or get_best_model()

        if not session.messages:
            session.messages.append(
                {"role": "system", "content": load_monitor_system_prompt()})

        if page_context:
            session.page_context = page_context

        content = user_message
        if page_context:
            content = _format_page_context(page_context) + "\n\n" + user_message
        session.messages.append({"role": "user", "content": content})
        session.touch()

        yield emit(EV.START, session_id=session.session_id)
        yield from self._loop(session, model)

    def resume_with_confirmation(
        self, session: AgentSession, approved: bool, *,
        model: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """用户对下单类工具二次确认后继续（Phase 4 用）。"""
        model = model or get_best_model()
        pending = session.pending_tool_call
        session.pending_tool_call = None
        if pending is None:
            yield emit(EV.ERROR, message="没有待确认的操作")
            return

        yield emit(EV.START, session_id=session.session_id)
        if not approved:
            session.messages.append({
                "role": "tool", "tool_call_id": pending.id,
                "content": "用户已取消该操作，未执行。"})
            yield emit(EV.TOOL_RESULT, id=pending.id, name=pending.name, ok=False)
        else:
            args = {**pending.args, "_confirmed": True}
            result = run_tool(pending.name, args)
            if result.get("widget"):
                yield emit(EV.WIDGET, widget=result["widget"])
            session.messages.append({
                "role": "tool", "tool_call_id": pending.id,
                "content": _tool_msg_content(result["summary"])})
            yield emit(EV.TOOL_RESULT, id=pending.id, name=pending.name,
                       ok=result.get("ok", True))
        session.touch()
        yield from self._loop(session, model)

    # ──────────────────── 内部 ────────────────────

    def _loop(self, session: AgentSession, model: str) -> Generator[str, None, None]:
        from agents.usage import USAGE
        tools = REGISTRY.openai_schemas(allowed=session.allowed_tools)
        turn_prompt = 0
        turn_completion = 0

        def _usage_event():
            return emit(EV.USAGE,
                        turn={"prompt_tokens": turn_prompt, "completion_tokens": turn_completion},
                        cumulative=USAGE.snapshot())

        for _round in range(self.MAX_ROUNDS):
            assistant_msg = None
            tool_calls: list[dict] = []
            try:
                from agents.llm_client import stream_chat
                for ev in stream_chat(session.messages, model=model, tools=tools):
                    if ev["type"] == "text":
                        if ev["content"]:
                            yield emit(EV.CHUNK, content=ev["content"])
                    elif ev["type"] == "done":
                        assistant_msg = ev["message"]
                        tool_calls = ev["tool_calls"]
                        p = ev.get("prompt_tokens", 0) or 0
                        c = ev.get("completion_tokens", 0) or 0
                        if p or c:
                            USAGE.record(model, p, c)
                            turn_prompt += p
                            turn_completion += c
            except Exception as e:
                logger.error(f"MoneyBill LLM 调用失败: {e}")
                yield emit(EV.ERROR, message=f"AI 调用失败：{e}")
                return

            if assistant_msg is not None:
                session.messages.append(assistant_msg)

            # 每轮 LLM 结束即上报一次用量（让前端更及时）
            yield _usage_event()

            if not tool_calls:
                yield emit(EV.DONE, session_id=session.session_id)
                return

            for tc in tool_calls:
                td = REGISTRY.get(tc["name"]) if REGISTRY.has(tc["name"]) else None
                yield emit(EV.TOOL_CALL, id=tc["id"], name=tc["name"], args=tc["args"])

                # 下单类 → 中断等前端确认
                if td and td.requires_confirmation and not tc["args"].get("_confirmed"):
                    preview = None
                    if td.preview_fn:
                        try:
                            preview = td.preview_fn(tc["args"])
                        except Exception as e:  # noqa: BLE001
                            preview = {"error": str(e)}
                    session.pending_tool_call = PendingToolCall(
                        tc["id"], tc["name"], tc["args"])
                    yield emit(EV.CONFIRM_REQUIRED, id=tc["id"], name=tc["name"],
                               args=tc["args"], preview=preview)
                    yield emit(EV.AWAIT_CONFIRM, session_id=session.session_id)
                    return

                # subagent → 子流透传（Phase 2）
                if td and td.is_subagent:
                    result = yield from self._run_subagent(tc, session)
                else:
                    result = run_tool(tc["name"], tc["args"])

                if result.get("widget"):
                    yield emit(EV.WIDGET, widget=result["widget"])
                session.messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": _tool_msg_content(result["summary"])})
                yield emit(EV.TOOL_RESULT, id=tc["id"], name=tc["name"],
                           ok=result.get("ok", True))

        yield _usage_event()
        yield emit(EV.DONE, session_id=session.session_id, reason="max_rounds")

    def _run_subagent(self, tc: dict, session: AgentSession):
        """把 subagent 工具转交 SubagentRunner，子进度透传，只回灌 summary（Phase 2 实现）。"""
        from agents.subagents import get_runner
        yield emit(EV.AGENT_HANDOFF, agent=tc["name"], args=tc["args"])
        runner = get_runner(tc["name"])
        result_summary = ""
        widgets: list = []
        for line in runner.run(tc["args"]):
            ev = json.loads(line)
            if ev.get("event") == "subagent_done":
                res = ev.get("result", {})
                result_summary = res.get("summary", "")
                widgets = res.get("widgets", []) or []
            else:
                yield line  # 子进度透传到主聊天流
        for w in widgets:
            yield emit(EV.WIDGET, widget=w)
        return {"ok": True, "summary": result_summary}
