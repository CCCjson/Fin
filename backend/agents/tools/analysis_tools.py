"""
分析类工具 —— 五维决策驾驶舱、ML 涨跌预测。
"""
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope


class GetCockpitScoreArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")


@tool(
    name="get_cockpit_score",
    description=(
        "对单只股票做五维体检（技术/基本面/情绪/ML/持仓），给出综合分(0-100)、"
        "买卖建议、各维度细节、动态止损位，以及按真实资金+风控算出的可执行加仓股数/金额。"
        "「这只股该不该买/怎么样」的单股快诊首选（已含 ML 维度，无需另调 predict_stock）；"
        "要更深的完整研判用 run_deep_stock，批量选股推荐用 recommend_stocks。"
    ),
    args_model=GetCockpitScoreArgs,
    category="analysis",
    group="core",
)
def get_cockpit_score(symbol: str) -> ToolEnvelope:
    from cockpit_engine.aggregator import CockpitAggregator
    from agents.widgets import cockpit_widget
    r = CockpitAggregator().aggregate(symbol)

    dims = r.get("dimensions", {})
    summary = {
        "symbol": r.get("symbol"),
        "name": r.get("name"),
        "composite": r.get("composite"),
        "recommendation": r.get("recommendation"),
        "price": (r.get("price") or {}).get("latest"),
        "stop_loss": r.get("stop_loss"),
        "current_position_pct": r.get("current_position_pct"),
        "suggested": r.get("suggested"),
        "dimensions": {k: {"score": v.get("score"), "detail": v.get("detail")}
                       for k, v in dims.items()},
    }
    return ToolEnvelope(data=summary, widget=cockpit_widget(r))


class PredictStockArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    forward_days: int = Field(5, ge=1, le=60, description="预测未来天数，默认 5")


@tool(
    name="predict_stock",
    description=(
        "用已训练的 LSTM+XGBoost 集成模型预测个股短期涨跌方向与置信度。"
        "仅在用户单独问「ML/模型怎么看涨跌」时用；综合诊断请用 get_cockpit_score（已含 ML 维度）。"
        "需该股已训练模型，否则会提示先训练。"
    ),
    args_model=PredictStockArgs,
    category="analysis",
    group="backtest_ml",
)
def predict_stock(symbol: str, forward_days: int = 5) -> ToolEnvelope:
    from prediction_engine.engine import PredictionEngine
    from agents.widgets import prediction_widget
    try:
        r = PredictionEngine().predict(symbol, forward_days=forward_days)
    except FileNotFoundError:
        return ToolEnvelope(business_result="negative",
                             message=f"{symbol} 尚未训练预测模型，无法预测。可在「股价预测」页面先训练。")
    except ValueError as e:
        return ToolEnvelope(business_result="negative", message=f"无法预测 {symbol}：{e}")
    name = r.get("name")
    summary = {
        "symbol": symbol,
        "direction": r.get("direction"),
        "confidence": r.get("confidence"),
        "predicted_return": r.get("predicted_return"),
        "forward_days": r.get("forward_days"),
    }
    return ToolEnvelope(data=summary, widget=prediction_widget(symbol, r, name=name))
