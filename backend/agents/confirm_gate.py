"""
确认门 —— requires_confirmation 工具的拦截(intercept)与用户确认后续跑(resume)。

风控/资金类工具唯一的人工闸口：拦截时不执行、只出预览；续跑时无条件走真实执行路径
（`_confirmed` 只是门闩信号，风控本身在工具函数内部无条件重跑，不因确认而被信任）。

从 orchestrator 拆出来是纯粹的代码搬运，逻辑与移动前逐字节一致。
"""
import json
import time
from typing import Generator, Optional

from agents.context import AgentSession, PendingToolCall
from agents.events import EV, emit
from agents.executor import run_tool
from agents.registry import REGISTRY
from agents.tool_descs import done_desc


def tool_msg_content(summary) -> str:
    return summary if isinstance(summary, str) else json.dumps(
        summary, ensure_ascii=False, default=str)


class ConfirmationGate:
    def intercept(self, tc: dict, td, session: AgentSession) -> Optional[Generator[str, None, None]]:
        """td.requires_confirmation 且未 _confirmed 时返回一个会 yield
        CONFIRM_REQUIRED/AWAIT_CONFIRM 并写 pending_tool_call 的 generator；
        否则返回 None（放行不拦截）。"""
        if not (td and td.requires_confirmation and not tc["args"].get("_confirmed")):
            return None
        return self._intercept_gen(tc, td, session)

    @staticmethod
    def _intercept_gen(tc: dict, td, session: AgentSession) -> Generator[str, None, None]:
        preview = None
        if td.preview_fn:
            try:
                preview = td.preview_fn(tc["args"])
            except Exception as e:  # noqa: BLE001
                preview = {"error": str(e)}
        session.pending_tool_call = PendingToolCall(tc["id"], tc["name"], tc["args"])
        yield emit(EV.CONFIRM_REQUIRED, id=tc["id"], name=tc["name"],
                   args=tc["args"], preview=preview)
        yield emit(EV.AWAIT_CONFIRM, session_id=session.session_id)

    def resume(
        self, session: AgentSession, approved: bool, *,
        tool_call_id: Optional[str], model: str,
    ) -> Generator[str, None, bool]:
        """resume_with_confirmation 的主体：校验 tool_call_id 匹配、执行/取消、
        写 tool 回复、record_result。返回 True 时调用方应接着跑 _loop 续跑对话；
        返回 False 表示直接结束（无 pending 操作 / confirm 的 id 对不上）。"""
        pending = session.pending_tool_call
        if pending is None:
            yield emit(EV.ERROR, message="没有待确认的操作")
            return False
        # 确认必须点名它批的是哪个调用：双开窗口/迟到重放的 confirm 不能
        # 误批「当前恰好挂着的那个」操作。不匹配则拒绝并保留 pending。
        # 空串/None 也算不匹配——契约上确认必须显式点名，缺省不放行（防绕过）。
        if tool_call_id != pending.id:
            yield emit(EV.ERROR,
                       message="该确认对应的操作已不存在（可能来自旧窗口），未执行任何操作")
            return False
        session.pending_tool_call = None

        yield emit(EV.START, session_id=session.session_id)
        if not approved:
            session.messages.append({
                "role": "tool", "tool_call_id": pending.id,
                "content": "用户已取消该操作，未执行。"})
            yield emit(EV.TOOL_RESULT, id=pending.id, name=pending.name, ok=False,
                       desc="已按你的意思取消这一步")
        else:
            args = {**pending.args, "_confirmed": True}
            _t0 = time.perf_counter()
            result = run_tool(pending.name, args)
            _elapsed_ms = int((time.perf_counter() - _t0) * 1000)
            try:
                from agents.trace import write_raw_tool_call
                write_raw_tool_call(
                    session.session_id, call_id=pending.id, name=pending.name,
                    args=args, raw=result.get("data", result.get("summary")),
                    ok=result.get("ok", True), elapsed_ms=_elapsed_ms)
            except Exception:  # noqa: BLE001 — 落原文不可影响主流程
                pass
            # 决策留痕（provenance）：MoneyBill 实际下单。
            #
            # ⚠️ 这里记的是**每一次经确认的工具调用**，不只是买卖 —— 「加自选股」
            # 「删自选股」也会落一行（它们没有 action/entry_price）。
            #
            # 🔴 **这一整批都不是 AI 建议**（P0-4）：确认门记的是「已经发生的操作」，
            # 而 P0-1 评的是「AI 的预测对不对」。所以按工具名分流成 execution（真下单/
            # 补录成交）与 ops（加自选股/建预警/改设置/编策略），**两类都不进胜率分母**。
            # 分类表在 `common/decision_kind.py`，新增受确认门的工具漏登记会被门禁咬。
            #
            # 早先靠「它们没有 action → 后验判 unable/no_action」被动挡住 —— 那只对
            # 非交易操作成立：`place_order` 的行**有 action 也有成交价**，一直是可评的，
            # 且与 `crypto_intel_engine/execution.py` 的回执**双重留痕同一笔单**
            # （实测 id 42/43 是同一秒同一张 ETH 挂单）。
            try:
                from agents.quality_guard import worst_turn_quality
                from agents.skills_loader import MONITOR_PROMPT_VERSION
                from common.decision_kind import kind_for_confirmed_tool
                from decision_log import record_decision
                _summ = result.get("data", {}) if isinstance(result, dict) else {}
                _summ = _summ if isinstance(_summ, dict) else {}
                # **不编 confidence**：这是一笔交易确认，MoneyBill 没有产出结构化的
                # 置信度数值，凭空塞一个 0-100 是撒谎。真正有价值、也诚实的是记下
                # 「这笔交易是在什么数据质量下确认的」——P0-3 校准与后验评估要据此
                # 分辨「数据降级时确认的单子」和「数据齐全时确认的单子」的胜率差异。
                _q = worst_turn_quality(session.turn_quality)
                record_decision(
                    source="moneybill",
                    entry_kind=kind_for_confirmed_tool(pending.name),
                    symbol=_summ.get("symbol") or pending.args.get("symbol"),
                    action=_summ.get("action") or pending.args.get("side"),
                    entry_price=_summ.get("price") or pending.args.get("price"),
                    model_id=model,
                    prompt_version=MONITOR_PROMPT_VERSION,
                    input_snapshot={**pending.args, "data_quality": _q},
                    output_summary=_summ,
                    executed=bool(_summ.get("executed")),
                    latency_ms=_elapsed_ms,
                    session_id=session.session_id,
                    turn_start_idx=session.turn_start_idx,
                )
            except Exception:  # noqa: BLE001 — 留痕不可影响主流程
                pass
            if result.get("widget"):
                yield emit(EV.WIDGET, widget=result["widget"])
            if result.get("navigate"):
                yield emit(EV.NAVIGATE, **result["navigate"])
            session.messages.append({
                "role": "tool", "tool_call_id": pending.id,
                "content": tool_msg_content(result["summary"])})
            _ok = result.get("ok", True)
            mon = session.turn_monitor
            _rec = mon.record_result(
                {"id": pending.id, "name": pending.name, "args": args},
                REGISTRY.get(pending.name) if REGISTRY.has(pending.name) else None,
                result, _elapsed_ms,
                msg_index=len(session.messages) - 1) if mon else None
            yield emit(EV.TOOL_RESULT, id=pending.id, name=pending.name, ok=_ok,
                       desc=done_desc(pending.name, pending.args, _ok),
                       **({"verdict": _rec.verdict, "elapsed_ms": _rec.elapsed_ms}
                          if _rec else {}))
        session.touch()
        return True
