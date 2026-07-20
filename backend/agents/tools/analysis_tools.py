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
        "take_profit": r.get("take_profit"),
        "current_position_pct": r.get("current_position_pct"),
        "suggested": r.get("suggested"),
        "dimensions": {k: {"score": v.get("score"), "detail": v.get("detail")}
                       for k, v in dims.items()},
    }
    _record_cockpit_decision(r)
    return ToolEnvelope(data=summary, widget=cockpit_widget(r))


def _record_cockpit_decision(r: dict) -> None:
    """决策留痕（provenance）：驾驶舱评级入 DecisionLog，供「上次驾驶舱说买，对了吗」归因。

    **为什么留痕接在工具层而不是 `aggregator.aggregate()` 里**：aggregate 有两个
    调用方，另一个是 `recommend_engine/scoring.py:17` 的 `_aggregate_many()` ——
    批量选股循环，4 线程、每轮几十只。写在 aggregate 里会让每跑一次选股就涌几十条
    「Jason 从没看见过的中间打分」进 DecisionLog，而 recommend_engine 自己已经把
    最终 BUY 记成 moneybill_recommend 了 → 胜率分母被中间产物泡掉、同一建议重复
    计数。接在这里语义也更准：**展现给 Jason 的建议才算一条决策**。

    留痕失败绝不影响主流程（同 decision_log 模块的一贯取舍）。
    """
    try:
        from cockpit_engine.scorer import SCORER_VERSION
        from decision_log import record_decision

        sizing = r.get("suggested") or {}
        record_decision(
            source="cockpit",
            symbol=r.get("symbol"),
            name=r.get("name"),
            # 全仓约定：cockpit 没有独立的 action 字段，action 就等于 recommendation；
            # composite(0-100) 当 confidence 用。**别除以 100** —— 这一列约定 0-100，
            # decision_log._warn_if_confidence_looks_normalized 会吼。
            action=r.get("recommendation"),
            recommendation=r.get("recommendation"),
            # P0-2 起这里是**钳后**的分（数据降级时被打到 CLAMP_CAP）——留痕跟着
            # 变诚实了。钳前的原始分在 output_summary.raw_composite 里。
            confidence=r.get("composite"),
            entry_price=(r.get("price") or {}).get("latest"),
            stop_loss=r.get("stop_loss"),
            take_profit=r.get("take_profit"),
            position_pct=r.get("suggested_position_pct"),
            model_id="rule:cockpit_scorer",
            prompt_version=SCORER_VERSION,
            input_snapshot={
                "dimensions": r.get("dimensions"),
                "weights_used": r.get("weights_used"),
                "available_dimensions": r.get("available_dimensions"),
                # 这次的分是拿多少权重的数据算出来的（light 档 = 0.6）
                "dimension_coverage": r.get("dimension_coverage"),
                "data_quality": r.get("data_quality"),
            },
            output_summary={
                "composite": r.get("composite"),
                # **P0-3 要它**：校准得知道「打压前模型说多少」。且钳的口径以后会
                # 改（CLAMP_CAP / core_degraded 的定义），只留钳后的分 = 把原始
                # 信息永久丢掉，将来重算都没得算。
                "raw_composite": r.get("raw_composite"),
                # P0-3：本次用的历史命中率校准因子（<30 样本时为 1.0=没校准）。
                # 留痕便于审计「这条建议的 confidence 被历史打了几折」。
                "calibration_factor": r.get("calibration_factor"),
                "adjustments": list(r.get("adjustments") or ()),
                "suggested": sizing,
            },
            risk_passed=bool(sizing.get("risk_passed")),
        )
    except Exception:  # noqa: BLE001 — 留痕不可影响主流程
        pass


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
