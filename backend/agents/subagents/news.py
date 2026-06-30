"""新闻/舆情解读 subagent —— 复用 NewsAnalyzer.generate_report_stream。"""
from typing import Generator

from agents.events import emit
from agents.subagents.base import SubagentRunner, relay_markdown


class NewsSubagent(SubagentRunner):
    name = "run_news_analysis"

    def run(self, args: dict) -> Generator[str, None, None]:
        symbol = args.get("symbol") or None
        market = args.get("market") or "a_share"

        from news_engine.analyzer import NewsAnalyzer
        from llm_config import get_cheap_model

        model = get_cheap_model()
        inner = NewsAnalyzer().generate_report_stream(
            symbol=symbol, market=market, model=model,
        )
        yield from relay_markdown(inner, agent=self.name, model=model, max_summary=1200)
