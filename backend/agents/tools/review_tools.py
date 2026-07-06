"""
复盘类工具 —— 包 review.service.ReviewService，读某日复盘 + 触发 AI 评分。

只读/计算，不写笔记（笔记是人工 UI 动作）。request_ai_score 只花 token、不下单，故不需二次确认。
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.widgets import metric_cards_widget


def _parse_date(s: str = None) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(s)
    except ValueError:
        return date.today()


class GetDailyReviewArgs(BaseModel):
    review_date: Optional[str] = Field(None, description="日期 YYYY-MM-DD，可选，默认今天")


@tool(
    name="get_daily_review",
    description=(
        "获取某日复盘数据：大盘指数、当日持仓表现、当日交易、当日信号、日盈亏、已有复盘评分。"
        "不填日期默认今天。用户问「今天/某天复盘怎么样、盘后总结」时调用。"
    ),
    args_model=GetDailyReviewArgs,
    category="review",
    group="review",
)
def get_daily_review(review_date: str = None) -> ToolEnvelope:
    from review.service import ReviewService
    d = _parse_date(review_date)
    data = ReviewService().get_review_data(d)

    review = data.get("review") or {}
    summary = {
        "date": data.get("date"),
        "daily_pnl": data.get("daily_pnl"),
        "daily_pnl_pct": data.get("daily_pnl_pct"),
        "positions_count": data.get("positions_count"),
        "trades_count": data.get("trades_count"),
        "signals_count": data.get("signals_total"),
        "indices": [{"name": i.get("name"), "change_pct": i.get("change_pct")}
                    for i in data.get("indices", []) if i.get("price") is not None],
        "self_score": review.get("self_score"),
        "ai_score": review.get("ai_score"),
        "composite_score": review.get("composite_score"),
    }
    pnl = data.get("daily_pnl")
    cards = [
        {"label": "当日盈亏", "value": (f"¥{pnl:,.0f}" if pnl is not None else "—"),
         "type": "return", "positive": (pnl or 0) >= 0},
        {"label": "持仓数", "value": str(data.get("positions_count", 0)), "type": "neutral"},
        {"label": "当日交易", "value": f"{data.get('trades_count', 0)} 笔", "type": "neutral"},
        {"label": "当日信号", "value": str(data.get("signals_total", 0)), "type": "neutral"},
        {"label": "综合评分", "value": (str(review.get("composite_score")) if review.get("composite_score") is not None else "未评分"),
         "type": "quality"},
    ]
    widget = metric_cards_widget(cards, title=f"📓 {data.get('date')} 复盘")
    return ToolEnvelope(data=summary, widget=widget)


class RequestReviewAiScoreArgs(BaseModel):
    review_date: Optional[str] = Field(None, description="日期 YYYY-MM-DD，可选，默认今天")


@tool(
    name="request_review_ai_score",
    description=(
        "为某日复盘请求 AI 教练评分：从纪律/仓位/时机/信号跟进/自我反思五维打分并给评语。"
        "不填日期默认今天。用户说「给今天的操作打个分/AI 点评一下我的交易」时调用。"
    ),
    args_model=RequestReviewAiScoreArgs,
    category="review",
    group="review",
)
def request_review_ai_score(review_date: str = None) -> ToolEnvelope:
    from review.service import ReviewService
    d = _parse_date(review_date)
    try:
        r = ReviewService().request_ai_score(d)
    except ValueError as e:
        return ToolEnvelope(business_result="negative", message=f"AI 评分失败：{e}")

    dims = (r.get("ai_dimension_scores") or {}).get("dimensions", {}) if r.get("ai_dimension_scores") else {}
    label_map = {"discipline": "纪律", "position": "仓位", "timing": "时机",
                 "signal_follow": "信号跟进", "reflection": "自我反思"}
    cards = [{"label": "综合", "value": f"{r.get('ai_score', '—')}/10", "type": "quality"}]
    for k, lab in label_map.items():
        dim = dims.get(k)
        if isinstance(dim, dict) and dim.get("score") is not None:
            cards.append({"label": lab, "value": f"{dim['score']}/10", "type": "quality"})
    widget = metric_cards_widget(cards, title=f"🎯 {d.isoformat()} AI 复盘评分")
    summary = {
        "date": d.isoformat(),
        "ai_score": r.get("ai_score"),
        "ai_score_reason": r.get("ai_score_reason"),
        "composite_score": r.get("composite_score"),
    }
    return ToolEnvelope(data=summary, widget=widget)
