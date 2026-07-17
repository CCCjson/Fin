"""
决策驾驶舱 — 确定性综合打分

输入 aggregator 产出的五维分（各 0-100，50=中性，None=缺失），
按权重加权得综合分；缺失维度自动重新归一化权重，不当 0 拉低。
"""
from typing import Dict, Optional

# 打分口径的版本戳 —— 落进 DecisionLog.prompt_version。
#
# 这条路径没有 LLM，所以「prompt 版本」的语义在这里是「产生这条建议的**判定口径**
# 版本」= 下面这张权重表 + 阈值的版本。**改 WEIGHTS 或 _recommendation 的阈值
# 必须 bump**，否则历史决策会挂着错误的口径戳，归因时无从分辨。
#
# 与 DecisionLog.engine_version 是两根轴：这个戳「当时怎么给的建议」，
# engine_version 戳「事后怎么判的对错」。
SCORER_VERSION = "rule:cockpit-v1"

# 五维权重（投资建议导向）
# ⚠️ 这组权重**从没回测验证过**（见 00-PLAN.md Backlog）。改动请连带 bump SCORER_VERSION。
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
        dynamic_levels: collect() 的 dynamic_levels（取 atr_stop_loss 作止损位、
            take_profit_levels 的 tp2 作止盈位）
        current_position_pct: 当前该股占总资金比例（用于建议加减仓）
        max_position_pct: 单股集中度上限（满分时的目标仓位），来自用户设置

    Returns:
        {composite, recommendation, suggested_position_pct, suggested_add_pct,
         current_position_pct, stop_loss, take_profit, weights_used, available_dimensions}
    """
    available = {k: v for k, v in dimensions.items() if v is not None}
    if not available:
        return {
            "composite": None,
            # 无维度可用 = 压根没评出东西。后验评估会把 "N/A" 判成
            # unable/action_not_directional（没法评），**不是判错** —— 这是对的。
            "recommendation": "N/A",
            "suggested_position_pct": 0.0,
            "suggested_add_pct": 0.0,
            "current_position_pct": round(current_position_pct, 1),
            "stop_loss": None,
            "take_profit": None,
            "weights_used": {},
            "available_dimensions": [],
        }

    # 仅用可用维度，权重重新归一化
    total_w = sum(WEIGHTS[k] for k in available)
    weights_used = {k: round(WEIGHTS[k] / total_w, 4) for k in available}
    composite = round(sum(available[k] * weights_used[k] for k in available), 1)

    stop_loss = None
    take_profit = None
    if isinstance(dynamic_levels, dict):
        stop_loss = dynamic_levels.get("atr_stop_loss") or dynamic_levels.get("trailing_stop")
        # 止盈取 tp2（三档里的中档）—— 因为 stock_analyzer.calculate_dynamic_levels
        # 算 risk_reward_ratio 时用的就是 tp2，取同一档才能和盈亏比口径一致。
        # 之前这里只捞止损把止盈丢了，导致后验评估的 first_hit 永远只能是
        # stop_loss/none，止盈那半根轴是瞎的。
        for lv in (dynamic_levels.get("take_profit_levels") or []):
            if isinstance(lv, dict) and lv.get("level") == 2:
                take_profit = lv.get("price")
                break

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
        "take_profit": take_profit,
        "weights_used": weights_used,
        "available_dimensions": list(available.keys()),
    }
