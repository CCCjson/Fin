"""深度个股研判 subagent —— 复用 AdvisorService（多源采集 + 多轮推理）。"""
from typing import Generator

from agents.events import emit
from agents.subagents.base import SubagentRunner, relay_markdown


class DeepStockSubagent(SubagentRunner):
    name = "run_deep_stock"

    def run(self, args: dict) -> Generator[str, None, None]:
        symbol = (args.get("symbol") or "").strip()
        if not symbol:
            yield emit("subagent_done", result={"summary": "缺少 symbol，无法深度研判", "widgets": []})
            return
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
