"""
Subagent 注册 —— 把 4 个复杂任务登记成对模型透明的「特殊工具」(is_subagent=True)。
import 本包即触发注册（agents/__init__.py 会 import 它）。

参数用 args_model 声明（同域工具的单一数据源约定），校验发生在
tool_dispatch.ToolDispatcher.dispatch（复用 executor.validate_tool_args），
不是这里的 fn=_noop——subagent 由 orchestrator 直接分流，从不走 run_tool。
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.registry import REGISTRY, ToolDef
from agents.subagents.deep_stock import DeepStockSubagent
from agents.subagents.news import NewsSubagent
from agents.subagents.report import ReportSubagent
from agents.subagents.report_sections import SECTION_SUBAGENTS
from agents.subagents.alpha_lab import AlphaLabSubagent

_RUNNERS = {
    r.name: r
    for r in ([DeepStockSubagent(), NewsSubagent(), ReportSubagent(), AlphaLabSubagent()]
              + [cls() for cls in SECTION_SUBAGENTS])
}


def get_runner(name: str):
    return _RUNNERS[name]


def _noop(**_kw):  # subagent 由 orchestrator 直接分流，不走 executor
    return {}


def _register(name, description, args_model):
    if REGISTRY.has(name):
        return
    REGISTRY.register(ToolDef(
        name=name, description=description, args_model=args_model,
        fn=_noop, category="subagent", group="deep_agents", is_subagent=True,
    ))


class DeepStockArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    enable_web_search: bool = Field(False, description="是否联网搜最新新闻，默认 false")


_register(
    "run_deep_stock",
    "对单只股票做【深度研判】：自动采集价格/指标/信号/形态/财务（可选联网新闻），再用最强模型给出完整投资分析。"
    "比 get_cockpit_score 更深更全，适合用户想要「深入分析/帮我详细看看某只股票」时。",
    DeepStockArgs,
)


class NewsArgs(BaseModel):
    symbol: Optional[str] = Field(None, description="可选，个股代码；不填则大盘整体新闻")
    market: Literal["a_share", "hk_stock", "us_stock"] = Field("a_share", description="市场，默认 a_share")


_register(
    "run_news_analysis",
    "【新闻/舆情深度解读】：抓取并用 AI 综合解读市场或个股的最新新闻与情绪，慢但全。"
    "适合「这些消息意味着什么/新闻面怎么看」；只要快速情绪分数（秒回）用 get_news_sentiment。",
    NewsArgs,
)


class ReportArgs(BaseModel):
    report_type: Literal["daily", "weekly", "monthly"] = Field("weekly", description="报告周期，默认 weekly")


_register(
    "run_research_report",
    "【投研报告】：生成多章节的深度投资研究报告（日报/周报/月报），汇总市场、信号、回测、持仓、新闻。"
    "用户说「帮我出一份周报/投研报告/复盘报告」时用。耗时较长。",
    ReportArgs,
)


# ── 五个报告章节（13.2 拆解）──────────────────────────────────────────────
# 都是「成稿长文」：出的是能直接写进报告的一章 Markdown，几十秒起步。
# 与之相对的是同域的「快查工具」（秒回一个数字/一张表），描述里必须点名，
# 否则模型会在 report_news / run_news_analysis / get_news_sentiment 之间挑花眼。

class ReportSectionArgs(BaseModel):
    report_type: Literal["daily", "weekly", "monthly"] = Field(
        "weekly", description="报告周期，默认 weekly")


_SECTION_DESCRIPTIONS = {
    "report_market": (
        "【报告章节·大盘与板块】成稿长文：写出「市场总览与情绪研判 + 板块热点与北向资金」两章。"
        "用户说「看看这周大盘/板块轮动/北向在买什么」时用。"
        "只要指数涨跌数字（秒回）用 get_market_overview。"
    ),
    "report_news": (
        "【报告章节·新闻舆情】成稿长文：写出「新闻深度分析与舆情研判」一章，含重大新闻检测与情感倾向。"
        "只要个股情绪分数（秒回）用 get_news_sentiment；"
        "要针对某只票的新闻深度解读用 run_news_analysis。"
    ),
    "report_positions": (
        "【报告章节·持仓诊断】成稿长文：逐只持仓股做技术面诊断、卖出预警与明日操作建议。"
        "只要持仓列表/浮盈亏（秒回）用 get_positions。"
    ),
    "report_strategy": (
        "【报告章节·回顾与策略】成稿长文：写出「上期推荐回顾（胜率/涨幅归因）+ 信号与策略表现」两章。"
        "只要某条信号的胜率统计（秒回）用 get_signal_stats。"
    ),
    "report_picks": (
        "【报告章节·买入推荐】成稿长文：多策略共振打分选出买入标的，逐只给入场价/止损/止盈与理由。"
        "耗时最长（按 4 只一批分批生成）。要快速选股候选用 recommend_stocks。"
    ),
}

for _name, _desc in _SECTION_DESCRIPTIONS.items():
    _register(_name, _desc, ReportSectionArgs)


class AlphaLabArgs(BaseModel):
    symbols: list[str] = Field(..., min_length=1, description="目标股票代码列表，如 ['600519.SH']")
    goal: str = Field("sharpe", description="优化目标，如 sharpe / return，默认 sharpe")
    max_iterations: int = Field(8, ge=1, le=20, description="最大迭代轮数，默认 8，最多 20")


_register(
    "run_alpha_lab",
    "【策略研发】：让 AI 自动生成交易策略代码→沙箱回测→评分→多轮迭代优化，产出最佳策略。"
    "用户说「帮我研发/优化一个策略、做个量化策略」时用。耗时较长（多轮 LLM）。",
    AlphaLabArgs,
)
