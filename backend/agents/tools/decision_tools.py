"""
决策留痕类工具 —— 查 DecisionLog，复盘「系统当时为什么这么建议」+「后来对了吗」。

复用 decision_log.query_decisions（样本）+ decision_log.get_decision_stats（胜率）。
来源：advisor(AI顾问) / cockpit(驾驶舱) / moneybill(对话下单) /
      moneybill_recommend(选股推荐) / report_picks(报告选股)。
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

_SOURCES = Literal["advisor", "cockpit", "moneybill", "moneybill_recommend", "report_picks"]


class GetDecisionHistoryArgs(BaseModel):
    symbol: Optional[str] = Field(None, description="可选：股票代码，如 600519.SH")
    source: Optional[_SOURCES] = Field(None, description="可选：决策来源")
    action: Optional[Literal["BUY", "SELL", "HOLD", "AGGREGATE"]] = Field(
        None, description="可选：动作类型")
    start_date: Optional[str] = Field(None, description="可选：起始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="可选：结束日期 YYYY-MM-DD")
    outcome_status: Optional[Literal["completed", "pending", "unable"]] = Field(
        None, description="可选：后验评估状态。completed=已评出对错，pending=窗口未满，unable=没法评")
    horizon: Literal[5, 20] = Field(20, description="胜率的评估窗口：5 或 20 个交易日，默认 20")
    limit: int = Field(10, ge=1, le=50, description="最多返回几条**样本**，默认 10，按时间倒序")


@tool(
    name="get_decision_history",
    description=(
        "查历史 AI 决策记录 + **历史胜率**（按来源分组）。可按股票、来源、动作、日期、"
        "评估状态过滤。回答「上次为什么建议我买XX / 之前推荐过什么 / "
        "**你的推荐历史胜率是多少 / 你说的准不准**」。"
        "来源: advisor=AI顾问, cockpit=驾驶舱, moneybill=对话下单, "
        "moneybill_recommend=选股推荐, report_picks=报告选股。"
        "返回的 stats 是**全量**胜率（不受 limit 影响）；decisions 只是最近几条样本。"
        "注意 win_rate=null 表示「一条都没法评」而不是「胜率 0%」，"
        "看 unable_breakdown 说明为什么评不了。"
        "calibration=置信度校准（**你说高置信度时实际准多少**，按分桶）+ 反哺因子；"
        "样本<30 时 calibration_factor=1.0（不校准）。"
    ),
    args_model=GetDecisionHistoryArgs,
    category="review",
    group="review",
)
def get_decision_history(symbol: Optional[str] = None, source: Optional[str] = None,
                         action: Optional[str] = None, start_date: Optional[str] = None,
                         end_date: Optional[str] = None, outcome_status: Optional[str] = None,
                         horizon: int = 20, limit: int = 10) -> ToolEnvelope:
    import decision_log

    limit = max(1, min(int(limit or 10), 50))
    r = decision_log.query_decisions(
        symbol=symbol, source=source, action=action,
        start_date=start_date, end_date=end_date,
        outcome_status=outcome_status, limit=limit,
    )
    # 胜率按**过滤条件全量**算，**不传 limit** —— limit 只管返回几条样本给 LLM 看。
    # 混淆会让「MoneyBill 推荐胜率」变成「最近 10 条的胜率」且随 limit 变，比没有还糟。
    stats = decision_log.get_decision_stats(
        symbol=symbol, source=source,
        start_date=start_date, end_date=end_date, horizon=horizon,
    )

    # P0-3 置信度校准：「你说高置信度时实际准多少」。校准是 per-source 的（cockpit 的
    # composite 才是结构化置信度），不指定 source 时默认看 cockpit。
    calibration = decision_log.compute_calibration(source=source or "cockpit", horizon=horizon)

    if not r.get("total"):
        # 注意这里**仍然带上 stats**：「有 12 条建议但全都没法评」是个**有内容的**
        # negative —— 让 LLM 能说「advisor 那 12 条都没记方向和入场价，评不了」，
        # 而不是干巴巴一句「没有记录」。
        return ToolEnvelope(
            business_result="negative",
            message="没有符合条件的决策记录。",
            data={"stats": stats, "calibration": calibration},
        )

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
            "prompt_version": d.get("prompt_version"),
            # 后验：后来对了吗
            "outcome_status": d.get("outcome_status"),
            "unable_reason": d.get("unable_reason"),
            "outcome_5d": d.get("outcome_5d"),
            "outcome_20d": d.get("outcome_20d"),
            "return_5d": d.get("return_5d"),
            "return_20d": d.get("return_20d"),
            "first_hit": d.get("first_hit"),
            "first_hit_days": d.get("first_hit_days"),
        }
        for d in r.get("decisions", [])
    ]
    return ToolEnvelope(data={"total": r["total"], "returned": len(decisions),
                              "stats": stats, "calibration": calibration,
                              "decisions": decisions})
