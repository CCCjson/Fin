"""
决策留痕类工具 —— 查 DecisionLog，复盘「系统当时为什么这么建议」。

复用 decision_log.query_decisions。
来源：advisor(AI顾问) / cockpit(驾驶舱) / moneybill(对话下单) / moneybill_recommend(选股推荐)。
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope


class GetDecisionHistoryArgs(BaseModel):
    symbol: Optional[str] = Field(None, description="可选：股票代码，如 600519.SH")
    source: Optional[Literal["advisor", "cockpit", "moneybill", "moneybill_recommend"]] = Field(
        None, description="可选：决策来源")
    action: Optional[Literal["BUY", "SELL", "HOLD", "AGGREGATE"]] = Field(
        None, description="可选：动作类型")
    start_date: Optional[str] = Field(None, description="可选：起始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="可选：结束日期 YYYY-MM-DD")
    limit: int = Field(10, ge=1, le=50, description="最多返回条数，默认 10，按时间倒序")


@tool(
    name="get_decision_history",
    description=(
        "查历史 AI 决策记录（建议/推荐/下单留痕），可按股票、来源、动作、日期过滤。"
        "回答「上次为什么建议我买XX / 之前推荐过什么 / 系统最近给过哪些建议」。"
        "来源: advisor=AI顾问, cockpit=驾驶舱, moneybill=对话下单, moneybill_recommend=选股推荐。"
    ),
    args_model=GetDecisionHistoryArgs,
    category="review",
    group="review",
)
def get_decision_history(symbol: Optional[str] = None, source: Optional[str] = None,
                         action: Optional[str] = None, start_date: Optional[str] = None,
                         end_date: Optional[str] = None, limit: int = 10) -> ToolEnvelope:
    import decision_log

    limit = max(1, min(int(limit or 10), 50))
    r = decision_log.query_decisions(
        symbol=symbol, source=source, action=action,
        start_date=start_date, end_date=end_date, limit=limit,
    )
    if not r.get("total"):
        return ToolEnvelope(business_result="negative", message="没有符合条件的决策记录。")

    # 精简回灌 LLM：去掉大字段（完整快照留在 /decisions 页面看）
    decisions = [
        {
            "created_at": d.get("created_at"),
            "source": d.get("source"),
            "symbol": d.get("symbol"),
            "name": d.get("name"),
            "action": d.get("action"),
            "recommendation": d.get("recommendation"),
            "confidence": d.get("confidence"),
            "entry_price": d.get("entry_price"),
            "stop_loss": d.get("stop_loss"),
            "reasons": d.get("reasons"),
            "executed": d.get("executed"),
            "model_id": d.get("model_id"),
        }
        for d in r.get("decisions", [])
    ]
    return ToolEnvelope(data={"total": r["total"], "returned": len(decisions),
                              "decisions": decisions})
