"""投研报告 subagent —— 复用 ReportGenerator.generate_multi_stream（多章节链式）。"""
import json
import uuid
from typing import Generator

from agents.events import emit
from agents.subagents.base import SubagentRunner, relay_markdown


class ReportSubagent(SubagentRunner):
    name = "run_research_report"

    def run(self, args: dict) -> Generator[str, None, None]:
        report_type = args.get("report_type") or "weekly"
        if report_type not in ("daily", "weekly", "monthly"):
            report_type = "weekly"

        from report_engine.data_collector import ReportDataCollector
        from report_engine.prompt_builder import ReportPromptBuilder
        from report_engine.generator import ReportGenerator
        from llm_config import get_cheap_model
        model = get_cheap_model()

        yield emit("collecting", message="正在收集市场数据并构建报告…")

        try:
            data = ReportDataCollector().collect(report_type=report_type)
            builder = ReportPromptBuilder()
            call_specs = builder.build_multi(data, report_type)
            report_id = f"rpt_{uuid.uuid4().hex[:12]}"
            label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(report_type, "周报")
            title = f"量化投资{label} ({data['period_start']} ~ {data['period_end']})"
            inner = ReportGenerator().generate_multi_stream(
                data=data, call_specs=call_specs, prompt_builder=builder,
                report_id=report_id, title=title, model=model,
                report_type=report_type, period_start=data["period_start"],
                period_end=data["period_end"],
                data_snapshot=json.dumps(data, ensure_ascii=False, default=str),
            )
        except Exception as e:  # noqa: BLE001
            yield emit("subagent_done", result={"summary": f"报告生成失败：{e}", "widgets": []})
            return

        yield from relay_markdown(inner, agent=self.name, model=model, max_summary=1500)
