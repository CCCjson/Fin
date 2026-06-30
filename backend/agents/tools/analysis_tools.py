"""
分析类工具 —— 五维决策驾驶舱、ML 涨跌预测。
"""
from agents.registry import tool


@tool(
    name="get_cockpit_score",
    description=(
        "对单只股票做五维体检（技术/基本面/情绪/ML/持仓），给出综合分(0-100)、"
        "买卖建议、各维度细节、动态止损位，以及按真实资金+风控算出的可执行加仓股数/金额。"
        "想快速诊断一只股票该不该买、买多少时，首选这个工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码，如 600519.SH"},
        },
        "required": ["symbol"],
    },
    category="analysis",
)
def get_cockpit_score(symbol: str) -> dict:
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
    return {"summary": summary, "widget": cockpit_widget(r)}


@tool(
    name="predict_stock",
    description="用已训练的 LSTM+XGBoost 集成模型预测个股短期涨跌方向与置信度。需该股已训练模型，否则会提示先训练。",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码，如 600519.SH"},
            "forward_days": {"type": "integer", "description": "预测未来天数，默认 5"},
        },
        "required": ["symbol"],
    },
    category="analysis",
)
def predict_stock(symbol: str, forward_days: int = 5) -> dict:
    from prediction_engine.engine import PredictionEngine
    from agents.widgets import prediction_widget
    try:
        r = PredictionEngine().predict(symbol, forward_days=forward_days)
    except FileNotFoundError:
        return {"summary": f"{symbol} 尚未训练预测模型，无法预测。可在「股价预测」页面先训练。"}
    except ValueError as e:
        return {"summary": f"无法预测 {symbol}：{e}"}
    name = r.get("name")
    return {
        "summary": {
            "symbol": symbol,
            "direction": r.get("direction"),
            "confidence": r.get("confidence"),
            "predicted_return": r.get("predicted_return"),
            "forward_days": r.get("forward_days"),
        },
        "widget": prediction_widget(symbol, r, name=name),
    }
