"""
MonitorOrchestrator —— MoneyBill 的编排循环（function-calling agent loop）。

同步 Generator，产 NDJSON 行；按 api/routes/advisor.py 的 thread/queue 模式在 worker
线程里被 drain。无 subagent/widget/确认 时退化为普通对话。

Phase 4 职责拆分：turn 开局副作用（系统提示/tool_groups/page_context/历史压缩/
TurnMonitor 重建）在 agents/turn_setup.py；确认门（拦截+续跑）在 agents/confirm_gate.py；
tool_call 路由（meta/subagent/普通工具）在 agents/tool_dispatch.py。本文件只保留
_loop 本体（纯粹的 Reason→Act→Observe 骨架）+ subagent 执行细节（因为紧耦合
session.turn_monitor 的 token 记账，搬出去意义不大，见 tool_dispatch.py 头部说明）。
"""
import json
import time
from typing import Generator, Optional

from loguru import logger

from llm_config import get_best_model
from agents.confirm_gate import ConfirmationGate, tool_msg_content
from agents.events import EV, emit
from agents.registry import REGISTRY
from agents.context import AgentSession
from agents.tool_descs import done_desc
from agents.tool_dispatch import ToolDispatcher
from agents.tool_envelope import ErrorCode, ToolEnvelope, to_legacy_dict
from agents.turn_setup import prepare_turn


def _sanitize_tool_args(args: Optional[dict]) -> dict:
    """剥掉模型提交的 `_` 前缀键 —— 服务端专属命名空间。

    `_confirmed` 等门闩状态只能由服务端（resume_with_confirmation）注入；
    模型若在 tool_call 参数里伪造 `_confirmed:true`，会直接跳过人工确认，
    所以进处理管线前一律剥除。"""
    return {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}


class MonitorOrchestrator:
    # 不是功能限制，只是防「模型无限循环调工具」的保险丝（Jason 定：轮数放开，token 烧就烧）。
    # 万一真撞上，_loop 末尾会强制一次无工具的收尾回答，绝不静默返回空。
    MAX_ROUNDS = 100

    def __init__(self) -> None:
        self.confirm_gate = ConfirmationGate()
        self.dispatcher = ToolDispatcher(self._run_subagent)

    def run_stream(
        self,
        session: AgentSession,
        user_message: str,
        *,
        model: Optional[str] = None,
        page_context: Optional[dict] = None,
    ) -> Generator[str, None, None]:
        model = model or get_best_model()
        prepare_turn(session, user_message, page_context)
        yield emit(EV.START, session_id=session.session_id)
        yield from self._loop(session, model)

    def resume_with_confirmation(
        self, session: AgentSession, approved: bool, *,
        tool_call_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """用户对下单类工具二次确认后继续。"""
        model = model or get_best_model()
        should_continue = yield from self.confirm_gate.resume(
            session, approved, tool_call_id=tool_call_id, model=model)
        if should_continue:
            yield from self._loop(session, model)

    # ──────────────────── 内部 ────────────────────

    def _loop(self, session: AgentSession, model: str) -> Generator[str, None, None]:
        from agents.usage import USAGE
        from agents.policy_checks import PolicyChecker, policy_check_enabled
        turn_prompt = 0
        turn_completion = 0
        mon = session.turn_monitor  # TurnMonitor（可能为 None=关闭），跨确认续跑存活
        policy_checker = PolicyChecker() if policy_check_enabled() else None
        policy_nudged = False  # 每 turn 只强制纠偏一次，避免死循环

        def _usage_event():
            return emit(EV.USAGE,
                        turn={"prompt_tokens": turn_prompt, "completion_tokens": turn_completion},
                        cumulative=USAGE.snapshot())

        def _trace(reason: str) -> None:
            """turn 终点统一落 trace（可回放审计），失败不影响主流程。"""
            from agents.trace import write_turn_trace
            write_turn_trace(session, model=model, reason=reason,
                             turn_usage={"prompt_tokens": turn_prompt,
                                         "completion_tokens": turn_completion})

        def _finalize(reason: str, notice: str) -> Generator[str, None, None]:
            """强制无工具收尾 + 用量/汇总/DONE（max_rounds 与硬熔断共用）。"""
            nonlocal turn_prompt, turn_completion
            try:
                p, c = yield from self._finalize_no_tools(session, model, notice=notice)
            except Exception as e:
                logger.error(f"MoneyBill 收尾调用失败: {e}")
                yield emit(EV.ERROR, message=f"AI 调用失败：{e}")
                _trace("llm_error")
                return
            turn_prompt += p
            turn_completion += c
            if mon:
                mon.record_llm_round(p, c)
            yield _usage_event()
            if mon:
                yield emit(EV.MONITOR, kind="turn_summary", **mon.summary_fields())
            yield emit(EV.DONE, session_id=session.session_id, reason=reason)
            _trace(reason)

        for _round in range(self.MAX_ROUNDS):
            # 协作取消：客户端已断开就到此为止。轮首是格式安全点（上一轮 tool
            # 回复已全部 append），直接停不发收尾 LLM 调用——听众都走了，别白烧。
            if session.cancel_event.is_set():
                logger.info(
                    f"MoneyBill 客户端断开，协作取消于 round={_round} "
                    f"session={session.session_id}")
                _trace("cancelled")
                return
            # 轮首监控决策（此刻上一轮 tool 回复已全部 append，注入/压缩格式安全）
            if mon:
                mon_events, budget_stop = mon.pre_round(session)
                for line in mon_events:
                    yield line
                if budget_stop:
                    logger.warning(
                        f"MoneyBill 触发 token 硬预算熔断 turn_tokens={mon.turn_tokens}")
                    yield from _finalize(
                        "token_budget",
                        "（系统提示：本轮 token 消耗已超硬性预算，已停止继续调用工具。"
                        "请立即基于以上已获得的工具结果直接给 Jason 最终回答："
                        "拿到了什么就答什么，没拿到的如实说明并给出下一步建议，"
                        "不要再尝试调用任何工具。）")
                    return
            # 每轮重算：load_toolgroup 可能中途扩容 allowed_tools
            tools = REGISTRY.openai_schemas(allowed=session.allowed_tools)
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
                        tool_calls = [
                            {**tc, "args": _sanitize_tool_args(tc.get("args"))}
                            for tc in ev["tool_calls"]]
                        p = ev.get("prompt_tokens", 0) or 0
                        c = ev.get("completion_tokens", 0) or 0
                        if p or c:
                            USAGE.record(model, p, c)
                            turn_prompt += p
                            turn_completion += c
                        if mon:
                            mon.record_llm_round(p, c)
                        logger.info(
                            f"[tokens] model={model} round={_round} prompt={p} "
                            f"completion={c} msgs={len(session.messages)} "
                            f"tools={len(tools)}")
            except Exception as e:
                logger.error(f"MoneyBill LLM 调用失败: {e}")
                yield emit(EV.ERROR, message=f"AI 调用失败：{e}")
                _trace("llm_error")
                return

            if assistant_msg is not None:
                session.messages.append(assistant_msg)

            # 每轮 LLM 结束即上报一次用量（让前端更及时）
            yield _usage_event()

            if not tool_calls:
                if policy_checker and not policy_nudged:
                    final_text = assistant_msg.get("content") if assistant_msg else None
                    violation = policy_checker.check(session, final_text or "")
                    if violation:
                        policy_nudged = True
                        session.messages.append({"role": "system", "content": violation})
                        continue  # 强制多跑一轮重新收尾，不发 DONE
                if mon:
                    yield emit(EV.MONITOR, kind="turn_summary", **mon.summary_fields())
                yield emit(EV.DONE, session_id=session.session_id)
                _trace("done")
                return

            for tc in tool_calls:
                td = REGISTRY.get(tc["name"]) if REGISTRY.has(tc["name"]) else None
                yield emit(EV.TOOL_CALL, id=tc["id"], name=tc["name"], args=tc["args"])

                # 元工具：加载工具组（改 session 状态，须在最先特判——不走确认门/去重）
                if self.dispatcher.is_meta(tc["name"]):
                    yield from self.dispatcher.dispatch_meta(tc, session, mon)
                    continue

                # 下单类 → 中断等前端确认
                blocked = self.confirm_gate.intercept(tc, td, session)
                if blocked is not None:
                    yield from blocked
                    # 确认中断也落一份（partial）；续跑结束会再落完整一份，同 turn_start_idx
                    _trace("await_confirm")
                    return

                # L1 重复调用拦截：同工具+同参数不真跑，回放首次结果摘录
                if mon:
                    dup = mon.check_duplicate(tc, td)
                    if dup:
                        session.messages.append({
                            "role": "tool", "tool_call_id": tc["id"],
                            "content": dup})
                        mon.record_duplicate(tc)
                        yield emit(EV.TOOL_RESULT, id=tc["id"], name=tc["name"],
                                   ok=True, desc="重复调用已拦截，沿用首次结果",
                                   verdict="duplicate", elapsed_ms=0)
                        continue

                # subagent → 子流透传 / 普通工具 → run_tool
                _t0 = time.perf_counter()
                result = yield from self.dispatcher.dispatch(tc, td, session)
                _elapsed_ms = int((time.perf_counter() - _t0) * 1000)

                from agents.trace import write_raw_tool_call
                write_raw_tool_call(
                    session.session_id, call_id=tc["id"], name=tc["name"],
                    args=tc["args"], raw=result.get("data", result.get("summary")),
                    ok=result.get("ok", True), elapsed_ms=_elapsed_ms,
                    is_subagent=bool(td and td.is_subagent))

                if result.get("widget"):
                    yield emit(EV.WIDGET, widget=result["widget"])
                if result.get("navigate"):
                    yield emit(EV.NAVIGATE, **result["navigate"])
                session.messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": tool_msg_content(result["summary"])})
                _ok = result.get("ok", True)
                _rec = mon.record_result(
                    tc, td, result, _elapsed_ms,
                    msg_index=len(session.messages) - 1) if mon else None
                yield emit(EV.TOOL_RESULT, id=tc["id"], name=tc["name"], ok=_ok,
                           desc=done_desc(tc["name"], tc["args"], _ok),
                           **({"verdict": _rec.verdict, "elapsed_ms": _rec.elapsed_ms}
                              if _rec else {}))

        # 保险丝触发：轮数异常多仍在调工具 —— 强制一次「无工具」收尾调用，
        # 保证 Jason 无论如何都能拿到一段文字回答（此前这里静默 DONE，前端表现为回答为空）。
        logger.warning(
            f"MoneyBill 触发轮数保险丝 MAX_ROUNDS={self.MAX_ROUNDS}，强制无工具收尾")
        yield from _finalize(
            "max_rounds",
            "（系统提示：本次工具调用轮数异常多，已停止继续调用。"
            "请立即基于以上已获得的工具结果直接给 Jason 最终回答："
            "拿到了什么就答什么，没拿到的如实说明并给出下一步建议，"
            "不要再尝试调用任何工具。）")

    def _finalize_no_tools(
        self, session: AgentSession, model: str, *, notice: str,
    ) -> Generator[str, None, tuple[int, int]]:
        """注入收尾提示并做一次不带 tools 的 LLM 调用（只能出文本），返回 (p, c)。

        max_rounds 保险丝与 token 硬预算熔断共用；异常向上抛，由调用方兜底。
        """
        from agents.usage import USAGE
        from agents.llm_client import stream_chat
        session.messages.append({"role": "system", "content": notice})
        assistant_msg = None
        p = c = 0
        for ev in stream_chat(session.messages, model=model):  # 不传 tools → 只能出文本
            if ev["type"] == "text":
                if ev["content"]:
                    yield emit(EV.CHUNK, content=ev["content"])
            elif ev["type"] == "done":
                assistant_msg = ev["message"]
                p = ev.get("prompt_tokens", 0) or 0
                c = ev.get("completion_tokens", 0) or 0
                if p or c:
                    USAGE.record(model, p, c)
        if assistant_msg is not None:
            session.messages.append(assistant_msg)
        return (p, c)

    @staticmethod
    def _envelope_from_subagent_result(res: dict) -> ToolEnvelope:
        """把 subagent_done.result 的 wire 形状（widgets 是 list，最多 1 个）适配回
        ToolEnvelope（widget 是单个）。解析失败（子层没走 emit_subagent_done 规范
        构造）保守地当成内部错误处理，而不是默认成功——subagent 不是 LLM 产出，
        这里没有"重试"的意义，兜底优先安全。"""
        payload = dict(res)
        widgets = payload.pop("widgets", None) or []
        if widgets and "widget" not in payload:
            payload["widget"] = widgets[0]
        try:
            return ToolEnvelope.model_validate(payload)
        except Exception:
            return ToolEnvelope(
                ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                error_detail={"raw": str(payload)[:500]},
                message="子任务返回格式异常，未能解析")

    def _run_subagent(self, tc: dict, session: AgentSession):
        """把 subagent 工具转交 SubagentRunner，子进度透传，只回灌 summary。

        契约：subagent_done.result 是 ToolEnvelope 序列化（见 subagents/base.py::
        emit_subagent_done）。异常/失败统一走 ok:False 回灌，让主 agent 走既有
        自愈路径（同普通工具，复用 to_legacy_dict）；tokens（子层 LLM 消耗）计入
        TurnMonitor 预算，否则 L3/L5 对最烧钱的调用失明。
        """
        from agents.subagents import get_runner
        yield emit(EV.AGENT_HANDOFF, agent=tc["name"], args=tc["args"])
        envelope = ToolEnvelope(
            ok=False, error_code=ErrorCode.INTERNAL_ERROR,
            error_detail={"message": "子任务未产生 subagent_done 事件"},
            message=f"子任务 {tc['name']} 执行失败，未产生输出")
        try:
            runner = get_runner(tc["name"])
            for line in runner.run(tc["args"]):
                ev = json.loads(line)
                if ev.get("event") == "subagent_done":
                    envelope = self._envelope_from_subagent_result(ev.get("result", {}))
                else:
                    yield line  # 子进度透传到主聊天流
        except Exception as e:  # noqa: BLE001 — 子层崩溃不许杀死主流
            logger.error(f"subagent {tc['name']} 执行异常: {e}")
            return {"ok": False,
                    "summary": f"子任务 {tc['name']} 执行异常：{e}。"
                               "请基于已有信息回答，或改用其他工具，不要原样重试。"}
        if envelope.tokens and session.turn_monitor:
            session.turn_monitor.record_subagent_tokens(envelope.tokens)
        # widget 不在这里 emit——to_legacy_dict 把它放进返回 dict 的 "widget" 键，
        # 调用方（_loop 里 tool_calls 循环的通用后处理）统一发 EV.WIDGET，避免重复。
        return to_legacy_dict(envelope)
