"""选股推荐工具 —— recommend_stocks（薄适配器）

业务逻辑已下沉到 recommend_engine（引擎+工具两层架构）：候选双源合并、ST/主板过滤、
可负担性闸门、盘中分钟线现算、并发深度打分、三重闸门 BUY 判定、决策留痕全在引擎里。
本文件只做：参数模型 + 调引擎 + 包 ToolEnvelope（含前端看板 widget + 回灌 LLM 的瘦身副本）。

注：_now_sh / _session_phase 从 recommend_engine.session re-export，供 market_tools /
intraday_tools / portfolio_tools 沿用旧 import 路径（`from agents.tools.recommend_tools
import _session_phase`），无需改动那些复用点。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.widgets import recommendation_board_widget
from recommend_engine import recommend
from recommend_engine.session import _now_sh, _session_phase  # noqa: F401 (向后兼容 re-export)


class RecommendStocksArgs(BaseModel):
    pool_id: Optional[Literal["sse50", "csi300", "csi500"]] = Field(
        None, description="可选：把候选限定在某指数池内；不填则全市场主板选股")
    limit: int = Field(5, ge=1, le=8, description="最多深度分析并推荐的新买入标的数，默认 5")
    min_strength: float = Field(0.3, ge=0, le=1, description="今日买入信号强度下限(0-1)，默认 0.3，过滤弱信号")


@tool(
    name="recommend_stocks",
    description=(
        "按今日行情给出选股建议。已持仓的给 SELL/HOLD 处置；未持仓且综合评级 BUY、"
        "按当前资金买得起、过风控的才给 BUY 推荐。盘中用分钟线实时算信号(标 provisional)，"
        "盘后用当日/最近日线信号；候选骨干来自基本面选股器(只留 5000 元买得起的主板股)。"
        "今日无信号/弱市时明确返回空仓观望，不硬凑、绝不凭记忆报股票。"
        "用户问「今天买什么/有啥可推荐/我的持仓要不要动/帮我选几只股」时调用。"
    ),
    args_model=RecommendStocksArgs,
    category="analysis",
    group="core",
)
def recommend_stocks(pool_id: Optional[str] = None, limit: int = 5,
                     min_strength: float = 0.3) -> ToolEnvelope:
    """按今日行情选股：持仓分流 SELL/HOLD、非持仓推 BUY，受账户资金硬约束。"""
    summary = recommend(pool_id=pool_id, max_new_buys=limit, min_strength=min_strength)

    # widget 用完整数据渲染前端看板；回灌 LLM 的 summary 用瘦身副本控 token
    widget = recommendation_board_widget(summary)
    buys = summary.get("buys") or []
    slim = {
        **summary,
        "buys": [{**b, "reasons": (b.get("reasons") or [])[:3]} for b in buys],
        "skipped_unaffordable": (summary.get("skipped_unaffordable") or [])[:8],
    }
    return ToolEnvelope(data=slim, widget=widget)
