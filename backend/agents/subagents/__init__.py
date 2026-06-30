"""
Subagent 注册 —— 把 4 个复杂任务登记成对模型透明的「特殊工具」(is_subagent=True)。
import 本包即触发注册（agents/__init__.py 会 import 它）。
"""
from agents.registry import REGISTRY, ToolDef
from agents.subagents.deep_stock import DeepStockSubagent
from agents.subagents.news import NewsSubagent
from agents.subagents.report import ReportSubagent
from agents.subagents.alpha_lab import AlphaLabSubagent

_RUNNERS = {
    r.name: r
    for r in [DeepStockSubagent(), NewsSubagent(), ReportSubagent(), AlphaLabSubagent()]
}


def get_runner(name: str):
    return _RUNNERS[name]


def _noop(**_kw):  # subagent 由 orchestrator 直接分流，不走 executor
    return {}


def _register(name, description, parameters):
    if REGISTRY.has(name):
        return
    REGISTRY.register(ToolDef(
        name=name, description=description, parameters=parameters,
        fn=_noop, category="subagent", is_subagent=True,
    ))


_register(
    "run_deep_stock",
    "对单只股票做【深度研判】：自动采集价格/指标/信号/形态/财务（可选联网新闻），再用最强模型给出完整投资分析。"
    "比 get_cockpit_score 更深更全，适合用户想要「深入分析/帮我详细看看某只股票」时。",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "股票代码，如 600519.SH"},
        "enable_web_search": {"type": "boolean", "description": "是否联网搜最新新闻，默认 false"},
    }, "required": ["symbol"]},
)

_register(
    "run_news_analysis",
    "【新闻/舆情解读】：抓取并用 AI 综合解读市场或个股的最新新闻与情绪。用户想了解「最近有什么消息/新闻面/舆情」时用。",
    {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "可选，个股代码；不填则大盘整体新闻"},
        "market": {"type": "string", "enum": ["a_share", "hk_stock", "us_stock"], "description": "市场，默认 a_share"},
    }},
)

_register(
    "run_research_report",
    "【投研报告】：生成多章节的深度投资研究报告（日报/周报/月报），汇总市场、信号、回测、持仓、新闻。"
    "用户说「帮我出一份周报/投研报告/复盘报告」时用。耗时较长。",
    {"type": "object", "properties": {
        "report_type": {"type": "string", "enum": ["daily", "weekly", "monthly"], "description": "报告周期，默认 weekly"},
    }},
)

_register(
    "run_alpha_lab",
    "【策略研发】：让 AI 自动生成交易策略代码→沙箱回测→评分→多轮迭代优化，产出最佳策略。"
    "用户说「帮我研发/优化一个策略、做个量化策略」时用。耗时较长（多轮 LLM）。",
    {"type": "object", "properties": {
        "symbols": {"type": "array", "items": {"type": "string"}, "description": "目标股票代码列表，如 ['600519.SH']"},
        "goal": {"type": "string", "description": "优化目标，如 sharpe / return，默认 sharpe"},
        "max_iterations": {"type": "integer", "description": "最大迭代轮数，默认 8"},
    }, "required": ["symbols"]},
)
