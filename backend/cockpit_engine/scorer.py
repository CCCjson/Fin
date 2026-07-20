"""
决策驾驶舱 — 确定性综合打分

输入 aggregator 产出的五维分（各 0-100，50=中性，None=缺失），
按权重加权得综合分；缺失维度自动重新归一化权重，不当 0 拉低。

**数据质量硬钳（2026-07-17，P0-2）**：传了 `quality` 且核心数据降级时，composite
被**代码强行打到 `CLAMP_CAP` 以下**，recommendation 与目标仓位随之重算。不是求
LLM 谨慎、不是 prompt 里写句「请注意」—— 这条路径压根没有 LLM，是纯代码说了算。

**「缺维度重新归一化」是刻意的，别改**（那条 2026-02 的设计是对的：缺失 ≠ 看空）。
但它的代价现在被显式化了：一维算出的 65 分与五维算出的 65 分，此前在下游长得
一模一样。`dimension_coverage` 就是那个此前不存在的区分。
"""
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:      # 只为类型标注 —— 运行时不 import，本模块保持零依赖纯函数
    from common.context_quality import DataQuality

# 打分口径的版本戳 —— 落进 DecisionLog.prompt_version。
#
# 这条路径没有 LLM，所以「prompt 版本」的语义在这里是「产生这条建议的**判定口径**
# 版本」= 下面这张权重表 + 阈值的版本。**改 WEIGHTS 或 _recommendation 的阈值
# 必须 bump**，否则历史决策会挂着错误的口径戳，归因时无从分辨。
#
# 与 DecisionLog.engine_version 是两根轴：这个戳「当时怎么给的建议」，
# engine_version 戳「事后怎么判的对错」。
#
# v2（2026-07-17）：加数据质量硬钳 + dimension_coverage。判定口径变了 → 必须 bump，
# 否则 v1 那些「没被钳过」的历史决策会和 v2 的混在一起算胜率。
# v3（2026-07-20，P0-3）：加历史命中率校准（confidence 反哺）。判定口径又变了 → 再 bump。
#   校准因子由调用方（aggregator）从 DecisionLog 历史胜率算出并传入；样本 <30 时恒为
#   1.0（口径不变），但一旦生效 composite 会被下调，故历史决策必须能按版本区分。
SCORER_VERSION = "rule:cockpit-v3"

# 核心数据降级时 composite 的上限。
#
# 60 落在 HOLD 区间（BUY 线是 65，见 `_recommendation`）——即：**数据不可信时，
# 不许给出买入级别的结论**。这个数与 `common.context_quality.clamp_confidence`
# 的默认 cap 是同一把尺子，有门禁 `test_clamp_cap_sits_below_buy_threshold` 钉着。
CLAMP_CAP = 60.0

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
                  max_position_pct: float = 0.5,
                  quality: Optional["DataQuality"] = None,
                  calibration_factor: float = 1.0) -> Dict:
    """
    Args:
        dimensions: {technical, ml, fundamental, sentiment, position} → 分值或 None
        dynamic_levels: collect() 的 dynamic_levels（取 atr_stop_loss 作止损位、
            take_profit_levels 的 tp2 作止盈位）
        current_position_pct: 当前该股占总资金比例（用于建议加减仓）
        max_position_pct: 单股集中度上限（满分时的目标仓位），来自用户设置
        quality: `common.context_quality.compute_quality` 的产出。给了且核心数据
            降级 → composite 被强行钳到 `CLAMP_CAP`。**不给 = 不钳**（老调用方
            行为不变）。
        calibration_factor: P0-3 历史命中率校准因子（0.5-1.0，1.0=不动）。由调用方
            （aggregator）从 DecisionLog 历史胜率算出（`decision_log.get_calibration_factor`）。
            **只下调 composite 不上抬**，作用在硬钳之前 —— 校准是「模型自信但历史不准
            就打折」，硬钳是「数据不可信就封顶」，两道各管各的，硬钳仍是最后安全网。
            纯函数不查库：本模块零依赖，样本量门槛/窗口都在调用侧决定。

    Returns:
        {composite, recommendation, suggested_position_pct, suggested_add_pct,
         current_position_pct, stop_loss, take_profit, weights_used,
         available_dimensions, dimension_coverage, raw_composite,
         calibration_factor, adjustments}

        - `dimension_coverage`: 实际可用维度占总权重的比例。**此前不存在** ——
          一维算出的 65 与五维算出的 65 在下游长得一模一样。批量选股走 `light=True`
          时系统性丢掉 ml+sentiment（40% 权重）却从不吭声，这个字段就是那个哑巴。
        - `raw_composite`: 钳之前的分。P0-3 校准要审计「打压前是多少」，
          且钳的口径以后会改 —— 只留钳后的分等于把原始信息永久丢掉。
        - `adjustments`: **稳定标识符**元组，不是给人看的文案（文案会改，标识符不会）。
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
            "dimension_coverage": 0.0,
            "raw_composite": None,
            "calibration_factor": 1.0,
            "adjustments": (),
        }

    # 仅用可用维度，权重重新归一化
    total_w = sum(WEIGHTS[k] for k in available)
    weights_used = {k: round(WEIGHTS[k] / total_w, 4) for k in available}
    composite = round(sum(available[k] * weights_used[k] for k in available), 1)

    # 这次的分是拿多少权重的数据算出来的。1.0 = 五维齐全；light 档 = 0.6。
    dimension_coverage = round(total_w / sum(WEIGHTS.values()), 4)

    # `raw_composite` = 未经任何调整的模型原始分（校准前 + 钳前）。P0-3 校准要审计
    # 「打压前是多少」，钳的口径以后也会改 —— 只留调整后的分等于把原始信息永久丢掉。
    raw_composite = composite
    adjustments: list[str] = []

    # ── P0-3 历史命中率校准（在硬钳之前）──
    # 「你历史上说得不准 → 这次的分先打个折」。只下调不上抬（factor <= 1.0），
    # 样本不足时调用方给的就是 1.0（不动）。放在硬钳之前：校准后仍要过硬钳这道
    # 安全网（数据降级 → 无论校准怎么算都压到 CLAMP_CAP）。
    if calibration_factor is not None and calibration_factor != 1.0:
        composite = round(composite * calibration_factor, 1)
        adjustments.append("confidence_calibrated_by_history")

    # ── 数据质量硬钳 ──
    # LLM 不参与这条路径，所以这里没有「求它诚实」的余地，也不需要 —— 直接改数。
    if quality is not None and quality.core_degraded and composite > CLAMP_CAP:
        composite = CLAMP_CAP
        adjustments.append("composite_capped_core_data_degraded")

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
        # composite / recommendation / 目标仓位 全部是**钳后**的值 —— 钳了分却
        # 照旧给 BUY 和满仓建议，这条硬约束就是摆设。
        "composite": composite,
        "recommendation": _recommendation(composite),
        "suggested_position_pct": target_pct,
        "suggested_add_pct": add_pct,
        "current_position_pct": round(current_position_pct, 1),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "weights_used": weights_used,
        "available_dimensions": list(available.keys()),
        "dimension_coverage": dimension_coverage,
        "raw_composite": raw_composite,
        "calibration_factor": calibration_factor,
        "adjustments": tuple(adjustments),
    }
