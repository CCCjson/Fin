"""五个报告章节 subagent —— 13.2 把 266 秒的全量周报拆开后的对话入口。

每个都是「贵一点的工具」：正文流式给用户看（chunk），只把一份结论摘要回灌进
模型 context。用户想只看板块轮动，就只跑 report_market，不必等整套。

Ch1「纵览 & 操作计划」没有对应工具——它天生需要全部前文。废掉全量报告后，
编排层从硬编码变成主 agent，纵览由 MoneyBill 看着这五份摘要亲自撰写。

为什么是 subagent 而不是普通工具：普通工具走 tool_call → tool_result，没有流式
通道；而一章正文有几千字，既要逐字流给用户，又不该整篇灌进模型 context。
"""
from collections.abc import Generator
from threading import Event

from agents.events import EV, emit
from agents.subagents.base import (
    SUMMARY_SYNTHESIZE,
    SubagentRunner,
    emit_subagent_done,
    relay_markdown,
)
from agents.tool_envelope import ErrorCode, ToolEnvelope

_VALID_REPORT_TYPES = ("daily", "weekly", "monthly")

# 每章开采前给用户的一句话（正式的逐章进度由 section_writer 的 collecting 事件发）
_COLLECT_HINT = {
    "market": "正在采集大盘、板块与北向资金数据…",
    "news": "正在采集市场与个股新闻，跑情感分析…",
    "positions": "正在采集持仓与持仓股技术信号…",
    "strategy": "正在采集上期推荐、信号与回测数据…",
    "picks": "正在筛选买入候选并打分…",
}


class ReportSectionSubagent(SubagentRunner):
    """章节 subagent 基类。子类只声明 name 与 section。"""

    section: str

    def run(self, args: dict, cancel_event: Event | None = None) -> Generator[str, None, None]:
        report_type = args.get("report_type") or "weekly"
        if report_type not in _VALID_REPORT_TYPES:
            report_type = "weekly"

        from llm_config import get_cheap_model
        from report_engine.data_collector import ReportDataCollector
        from report_engine.section_writer import write_section

        model = get_cheap_model()
        yield emit(EV.AGENT_PROGRESS, agent=self.name, message=_COLLECT_HINT[self.section])

        try:
            collect = getattr(ReportDataCollector(), f"collect_{self.section}")
            data = collect(report_type=report_type)
        except Exception as e:  # noqa: BLE001 — 采集失败是技术性失败，走 ok:False 自愈
            yield emit_subagent_done(ToolEnvelope(
                ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                error_detail={"exception_type": type(e).__name__, "message": str(e)},
                message=f"[{self.name}] 数据采集失败：{e}"))
            return

        self._after_collect(data)

        inner = write_section(self.section, data, report_type=report_type,
                              model=model, cancel_event=cancel_event)
        # max_summary 比旧全量报告的 1500 小：五章摘要都要进 context，且 MoneyBill
        # 写纵览只需要结论，不需要正文。
        yield from relay_markdown(inner, agent=self.name, model=model, max_summary=600,
                                  summary_prefix=self._summary_prefix(data))

    def _after_collect(self, data: dict) -> None:
        """采完数据、成稿之前的钩子。默认什么都不做。"""

    def _summary_prefix(self, data: dict) -> str:
        """摘要前缀 = 收尾政策 + 本章涉及的标的清单。

        清单不是给人看的，是给 `policy_checks._backed_symbols` 看的：它只扫工具结果
        文本来判断主 agent 提到的代码「有没有依据」。正文只截 600 字进 context，
        MoneyBill 写纵览时引用的代码很可能已被截掉，没这行就会被判成凭记忆瞎报、
        强制重写一轮。
        """
        symbols = self._symbols_in_scope(data)
        if not symbols:
            return SUMMARY_SYNTHESIZE
        return SUMMARY_SYNTHESIZE + "\n【本章涉及标的】" + "、".join(symbols)

    @staticmethod
    def _symbols_in_scope(data: dict) -> list[str]:
        top = data.get("top_stocks") or {}
        buckets = [
            (data.get("portfolio") or {}).get("positions") or [],
            top.get("buy_recommendations") or [],
            top.get("sell_warnings") or [],
            (data.get("previous_report") or {}).get("recommendations") or [],
        ]
        seen: dict[str, None] = {}
        for bucket in buckets:
            for item in bucket:
                sym = (item or {}).get("symbol")
                if sym:
                    seen.setdefault(sym, None)
        return list(seen)


class ReportMarketSubagent(ReportSectionSubagent):
    name = "report_market"
    section = "market"


class ReportNewsSubagent(ReportSectionSubagent):
    name = "report_news"
    section = "news"


class ReportPositionsSubagent(ReportSectionSubagent):
    name = "report_positions"
    section = "positions"


class ReportStrategySubagent(ReportSectionSubagent):
    name = "report_strategy"
    section = "strategy"


class ReportPicksSubagent(ReportSectionSubagent):
    name = "report_picks"
    section = "picks"

    def _after_collect(self, data: dict) -> None:
        """推荐一算出来就留痕，不等成稿——标的是选出来的，不是写出来的。

        这批 DecisionLog 就是下次 report_strategy 里「上期推荐回顾」的数据源。
        """
        from report_engine.picks_log import record_picks
        record_picks((data.get("top_stocks") or {}).get("buy_recommendations") or [])


SECTION_SUBAGENTS = [
    ReportMarketSubagent, ReportNewsSubagent, ReportPositionsSubagent,
    ReportStrategySubagent, ReportPicksSubagent,
]
