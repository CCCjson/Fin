"""
决策驾驶舱 — 确定性综合打分

输入 aggregator 产出的五维分（各 0-100，50=中性，None=缺失），
按权重加权得综合分；缺失维度自动重新归一化权重，不当 0 拉低。
"""
from typing import Dict, Optional

# 五维权重（投资建议导向）
WEIGHTS = {
    "technical": 0.30,   # 技术面
    "ml": 0.25,          # ML 预测
    "fundamental": 0.20,  # 基本面
    "sentiment": 0.15,   # 新闻情感
    "position": 0.10,    # 持仓/风险
}


def _recommendation(composite: float) -> str:
    if composite >= 65:
        return "BUY"
    if composite >= 45:
        return "HOLD"
    return "SELL"


def _target_position_pct(composite: float, max_pct: float = 0.5) -> float:
    """
    建议目标仓位（占总资金 %）。
    composite 50→0%，100→单股上限(max_pct×100%)。HOLD/SELL 区间(<45)不持仓。
    具体金额/股数由 trading_engine.position_sizing.size_position 按真实资金换算。
    """
    cap = max_pct * 100.0
    if composite < 45:
        return 0.0
    target = max(0.0, (composite - 50) / 50.0) * cap
    return round(min(target, cap), 1)


def score_cockpit(dimensions: Dict[str, Optional[float]],
                  dynamic_levels: Optional[Dict] = None,
                  current_position_pct: float = 0.0,
                  max_position_pct: float = 0.5) -> Dict:
    """
    Args:
        dimensions: {technical, ml, fundamental, sentiment, position} → 分值或 None
        dynamic_levels: collect() 的 dynamic_levels（取 atr_stop_loss 作止损位）
        current_position_pct: 当前该股占总资金比例（用于建议加减仓）
        max_position_pct: 单股集中度上限（满分时的目标仓位），来自用户设置

    Returns:
        {composite, recommendation, suggested_position_pct, suggested_add_pct,
         current_position_pct, stop_loss, weights_used, available_dimensions}
    """
    available = {k: v for k, v in dimensions.items() if v is not None}
    if not available:
        return {
            "composite": None,
            "recommendation": "N/A",
            "suggested_position_pct": 0.0,
            "suggested_add_pct": 0.0,
            "current_position_pct": round(current_position_pct, 1),
            "stop_loss": None,
            "weights_used": {},
            "available_dimensions": [],
        }

    # 仅用可用维度，权重重新归一化
    total_w = sum(WEIGHTS[k] for k in available)
    weights_used = {k: round(WEIGHTS[k] / total_w, 4) for k in available}
    composite = round(sum(available[k] * weights_used[k] for k in available), 1)

    stop_loss = None
    if isinstance(dynamic_levels, dict):
        stop_loss = dynamic_levels.get("atr_stop_loss") or dynamic_levels.get("trailing_stop")

    # 目标仓位 vs 当前已持仓占比 → 还需加仓多少（已达/超目标则不加仓）
    target_pct = _target_position_pct(composite, max_position_pct)
    add_pct = round(max(0.0, target_pct - current_position_pct), 1) if target_pct > 0 else 0.0

    return {
        "composite": composite,
        "recommendation": _recommendation(composite),
        "suggested_position_pct": target_pct,
        "suggested_add_pct": add_pct,
        "current_position_pct": round(current_position_pct, 1),
        "stop_loss": stop_loss,
        "weights_used": weights_used,
        "available_dimensions": list(available.keys()),
    }
