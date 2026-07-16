"""
涨停信号预测工具 —— 涨停池复盘查询 + 次日候选池预测。

与 recommend_stocks 严格分工（见 skills/monitor.md「涨停候选池预测纪律」）：
recommend_stocks 回答"该买哪些好公司"（价值选股逻辑），这里回答"明天哪些票可能
有涨停行情"（投机/动量逻辑）。候选来源、打分维度完全独立，不共享、不混用。
"""
from typing import Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.widgets import limit_up_pool_widget, limit_up_candidates_widget
from limit_up_engine.service import get_pool_overview, get_candidates


class GetLimitUpPoolArgs(BaseModel):
    trade_date: Optional[str] = Field(None, description="交易日 YYYYMMDD，不填默认今天")
    include_zhaban: bool = Field(True, description="是否包含炸板池数据")


@tool(
    name="get_limit_up_pool",
    description=(
        "查询今日/指定日涨停股池复盘：涨停家数、连板梯队分布、炸板率、赚钱效应"
        "（昨日涨停股今日表现）。用户问「今天涨停多少家/连板梯队怎样/炸板严不严重/"
        "昨天涨停的今天表现咋样」时调用。这是复盘/参考数据，不是买入候选——已封板"
        "的涨停股不代表可以买入，想要能买的候选请用 predict_limit_up_candidates。"
    ),
    args_model=GetLimitUpPoolArgs,
    category="analysis",
    group="market_sentiment",
)
def get_limit_up_pool(trade_date: Optional[str] = None, include_zhaban: bool = True) -> ToolEnvelope:
    overview = get_pool_overview(trade_date, include_zhaban)
    widget = limit_up_pool_widget(overview)
    slim = {**overview, "top_boards": overview.get("top_boards", [])[:10]}
    slim.pop("top_zhaban", None)
    return ToolEnvelope(data=slim, widget=widget)


class PredictLimitUpArgs(BaseModel):
    limit: int = Field(10, ge=1, le=20, description="返回候选数量上限，默认10")


@tool(
    name="predict_limit_up_candidates",
    description=(
        "基于规则打分模型，给出次日涨停候选池排名——候选只包含「当前未封板、能正常"
        "买入」的股票（强势股池/准涨停扫描/昨涨停今仍强势），已经封板买不进的涨停股"
        "不会出现在候选里。这是排序打分，非100%准确预测，仅供参考不构成投资建议。"
        "用户问「明天有哪些涨停苗头/哪些票可能继续涨停」时调用。"
    ),
    args_model=PredictLimitUpArgs,
    category="analysis",
    group="market_sentiment",
)
def predict_limit_up_candidates(limit: int = 10) -> ToolEnvelope:
    result = get_candidates(target_date_str=None, top_n=limit)
    widget = limit_up_candidates_widget(result)
    slim = {
        **result,
        "candidates": [
            {**c, "reasons": c["reasons"][:3]} for c in result.get("candidates", [])
        ],
    }
    if not slim.get("candidates"):
        return ToolEnvelope(business_result="negative", data=slim, message="今日无涨停候选池数据", widget=widget)
    return ToolEnvelope(data=slim, widget=widget)
