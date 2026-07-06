"""
深度个股研判 subagent。

v2（默认）：窄工具集 mini-ReAct 循环——按需调用真实工具（日线/实时行情/五维体检/
新闻情绪/信号/知识库）取数据，而不是像 v1 那样把 AdvisorPromptBuilder 的最多 11
个段落一次性无脑塞进一个大 prompt（数据丰富时能到 1500-3000+ token）。复用
MonitorOrchestrator._loop 的 ReAct 骨架（同一套 executor 参数校验/风控基础设施），
只是套一个隔离的 mini AgentSession + 窄 allowed_tools + 独立系统提示，内部的
tool_call/tool_result/widget/usage 事件被收敛，只把最终文本流式转发给用户
（不泄露内部工具名——Jason 定的产品原则）。

回滚开关：环境变量 AGENT_DEEP_STOCK_V2=off 时退回 v1（包一层 advisor_engine.AdvisorService）。
"""
import json
import os
import uuid
from typing import Generator

from agents.context import AgentSession
from agents.events import EV, emit
from agents.subagents.base import SubagentRunner, emit_subagent_done, relay_markdown
from agents.tool_envelope import ErrorCode, ToolEnvelope

# 窄工具集：只读、不含任何 requires_confirmation 工具（mini-session 没有真实 HTTP
# 往返可以中断等确认，误含确认类工具会导致 subagent 静默卡死在待确认状态）。
# 有意不注册进 agents/tool_groups.py 的 TOOL_GROUPS——那是主循环 load_toolgroup
# 自助扩容用的互斥分组，这几个工具本来就已经分别属于 CORE_TOOLS/news/signals/
# knowledge_web，重复归组会触发 tool_groups._registry_groups() 的漂移检测报错
# （每个工具的 group 只能有一个，不能既属于某个 TOOL_GROUPS 组又被这里复用）。
_DEEP_STOCK_TOOLS = {
    "get_daily_data", "get_realtime_quote", "get_cockpit_score",
    "get_news_sentiment", "get_stock_signals", "search_knowledge",
}
_MAX_SUMMARY = 1500


def _deep_stock_v2_enabled() -> bool:
    return os.getenv("AGENT_DEEP_STOCK_V2", "on").lower() not in ("off", "0", "false")


class DeepStockSubagent(SubagentRunner):
    name = "run_deep_stock"

    def run(self, args: dict) -> Generator[str, None, None]:
        symbol = (args.get("symbol") or "").strip()
        if not symbol:
            yield emit_subagent_done(ToolEnvelope(
                ok=False, error_code=ErrorCode.VALIDATION_ERROR,
                message="缺少 symbol，无法深度研判"))
            return
        if _deep_stock_v2_enabled():
            yield from self._run_v2(symbol)
        else:
            yield from self._run_v1(symbol, args)

    def _run_v2(self, symbol: str) -> Generator[str, None, None]:
        from agents.orchestrator import MonitorOrchestrator
        from agents.skills_loader import load_subagent_prompt
        from llm_config import get_best_model

        model = get_best_model()
        mini = AgentSession(session_id=f"ds_{symbol}_{uuid.uuid4().hex[:8]}")
        mini.messages = [
            {"role": "system", "content": load_subagent_prompt("deep_stock")},
            {"role": "user", "content": f"对 {symbol} 做深度研判并给出操作建议。"},
        ]
        mini.allowed_tools = set(_DEEP_STOCK_TOOLS)
        mini.turn_start_idx = 0
        # 不走 run_stream（会经 prepare_turn 把 tool_groups 的 CORE_TOOLS 并进
        # allowed_tools，冲掉窄工具集），直接跑 _loop 本体。
        orch = MonitorOrchestrator()

        acc: list[str] = []
        widget = None
        err_msg = None
        tokens = 0
        for line in orch._loop(mini, model):
            ev = json.loads(line)
            et = ev.get("event")
            if et == EV.CHUNK:
                c = ev.get("content", "")
                if c:
                    acc.append(c)
                    yield emit(EV.CHUNK, content=c)
            elif et == EV.WIDGET:
                widget = ev.get("widget")
            elif et == EV.USAGE:
                turn = ev.get("turn") or {}
                tokens = (turn.get("prompt_tokens", 0) or 0) + (turn.get("completion_tokens", 0) or 0)
            elif et == EV.ERROR:
                err_msg = ev.get("message")
            # tool_call/tool_result/monitor/confirm_*/done：内部实现细节，不透传
            # 给用户（deep_stock 窄工具集全部只读，不会触发确认门）。

        text = "".join(acc).strip()
        if err_msg and not text:
            yield emit_subagent_done(ToolEnvelope(
                ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                error_detail={"message": err_msg},
                message=f"[{self.name}] 执行失败：{err_msg}", tokens=tokens))
            return
        if not text:
            yield emit_subagent_done(ToolEnvelope(
                business_result="negative",
                message=f"[{self.name}] 没有产生输出", tokens=tokens))
            return

        body = text[:_MAX_SUMMARY] + ("…(已截断)" if len(text) > _MAX_SUMMARY else "")
        summary = (
            "[以下报告已完整展示给用户，仅作上下文存档。"
            "收尾时禁止复述其中的指标/价位/结论，禁止另给操作建议]\n" + body
        )
        yield emit_subagent_done(ToolEnvelope(message=summary, widget=widget, tokens=tokens))

    def _run_v1(self, symbol: str, args: dict) -> Generator[str, None, None]:
        """v1（回滚路径）：AdvisorPromptBuilder 一次性塞 11 段数据 + AdvisorService。"""
        enable_web = bool(args.get("enable_web_search", False))

        from advisor_engine.service import AdvisorService
        from llm_config import get_best_model

        model = get_best_model()
        svc = AdvisorService()
        sid = svc.create_session(symbol)
        inner = svc.chat_stream(
            session_id=sid, user_message=None,
            enable_web_search=enable_web, model=model,
        )
        yield from relay_markdown(inner, agent=self.name, model=model, max_summary=1500)
