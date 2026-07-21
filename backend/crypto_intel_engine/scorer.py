"""排雷层打分器 —— 加密独有的「这币该不该碰」体检（免费源，v1 做薄）。

对应 Jason 的判断「估值在加密里的用途是排雷不是选股」：不算内在价值，只用可量化的
免费信号识别明显的坑——供应稀释、大量未流通代币（解锁砸盘）、开发停滞、小市值。

两个入口：
- `market_context()`：市场级情绪/大势（恐慌贪婪 + BTC 主导率 + 总市值）。无需 symbol。
- `screen_coin(symbol)`：单币排雷体检 → 排雷分(0-100，越高越安全) + 逐维 + 红旗 + 结论。

排雷分是**透明扣分制**，不是黑盒。主流币（BTC/ETH）天然高分。

## v1 做薄的边界（诚实标注）
- 免费深度链上（MVRV/SOPR/活跃地址/巨鲸）未接（Glassnode/Nansen 付费）。
- 代币解锁精确时间表（DefiLlama emissions）未接——用 FDV/市值比做**解锁悬顶代理**
  （比值远大于 1 = 大量代币未流通 = 潜在稀释）。主流币无解锁，够用；碰山寨要深挖再接。
- 定性（团队/叙事/白皮书）走现有 `knowledge_engine` RAG，不在本打分器。
"""
from typing import Any

from loguru import logger

# 恐慌贪婪分档（供 MoneyBill 直接引用语义）
_FNG_BANDS = [
    (0, 25, "极度恐惧（历史上常是波段买点）"),
    (25, 45, "恐惧"),
    (45, 55, "中性"),
    (55, 75, "贪婪"),
    (75, 101, "极度贪婪（过热，波段注意减仓）"),
]


def _fng_label(value: int) -> str:
    for lo, hi, label in _FNG_BANDS:
        if lo <= value < hi:
            return label
    return "未知"


def market_context() -> dict[str, Any]:
    """市场级大势/情绪快照：恐慌贪婪 + BTC 主导率 + 总市值。单项失败不拖垮整体。"""
    from acquisition.markets.crypto_intel import coingecko_global, fear_greed_index

    ctx: dict[str, Any] = {}
    try:
        fng = fear_greed_index(limit=1)
        v = fng[0]["value"] if fng else None
        ctx["fear_greed"] = {"value": v, "label": _fng_label(v)} if v is not None else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"恐慌贪婪抓取失败: {e}")
        ctx["fear_greed"] = None
    try:
        g = coingecko_global()
        ctx["btc_dominance"] = round((g.get("market_cap_percentage") or {}).get("btc", 0), 2)
        ctx["total_market_cap_usd"] = (g.get("total_market_cap") or {}).get("usd")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"CoinGecko global 抓取失败: {e}")
        ctx["btc_dominance"] = None
        ctx["total_market_cap_usd"] = None
    return ctx


def screen_coin(symbol: str) -> dict[str, Any]:
    """单币排雷体检。返回 {symbol, coingecko_id, ambiguous, score, verdict, flags, dimensions}。

    score 0-100 越高越安全；verdict = pass(≥75) / caution(50-74) / avoid(<50) / unknown。
    查不到该币（解析失败）时 verdict='unknown'、score=None。
    """
    from acquisition.markets.crypto_intel import coingecko_coin
    from crypto_intel_engine.resolver import base_asset, resolve_coingecko_id

    base = base_asset(symbol)
    coin_id, ambiguous = resolve_coingecko_id(symbol)
    result: dict[str, Any] = {
        "symbol": symbol, "base_asset": base, "coingecko_id": coin_id,
        "ambiguous": ambiguous, "score": None, "verdict": "unknown",
        "flags": [], "dimensions": {},
    }
    if not coin_id:
        result["flags"].append(f"无法解析 {base} 到 CoinGecko（排雷数据缺失）")
        return result

    try:
        coin = coingecko_coin(coin_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"排雷取数失败 {symbol}({coin_id}): {e}")
        result["flags"].append("排雷取数失败（网络/限速）")
        return result

    md = coin.get("market_data", {}) or {}
    dev = coin.get("developer_data", {}) or {}
    dims, score, flags = score_dimensions(md, dev)
    result["dimensions"] = dims
    result["score"] = score
    result["verdict"] = verdict_of(score)
    if ambiguous:
        flags = [*flags, f"⚠️ {base} 由 symbol 回退匹配，可能撞名，请核对 coingecko_id"]
    result["flags"] = flags
    return result


def verdict_of(score: int | None) -> str:
    """排雷分 → 结论。None=unknown / ≥75 pass / 50-74 caution / <50 avoid。"""
    if score is None:
        return "unknown"
    return "pass" if score >= 75 else "caution" if score >= 50 else "avoid"


# ════════════════════ 币版驾驶舱：融合打分（择时三维 + 排雷否决闸）════════════════════
#
# 对标股票 `cockpit_engine.scorer.score_cockpit`，但维度换成加密两条腿的择时层：
# 技术择时 / 衍生品情绪 / 大势闸门。排雷层（screen_coin）**当否决闸**，不当加权维——
# 择时再好，排雷判 avoid（供应无上限/解锁悬顶/开发停滞）就强制压到 HOLD 以下，不给买。
#
# 阈值/仓位/硬钳口径与股票一致（复用 cockpit_engine.scorer 的纯函数，那模块零依赖）。
CRYPTO_SCORER_VERSION = "rule:crypto-cockpit-v1"

# 择时三维权重。技术择时为主，大势闸门次之（熊市压做多），衍生品情绪辅助。
CRYPTO_WEIGHTS = {"technical": 0.45, "regime": 0.30, "derivatives": 0.25}

# 排雷 avoid 时 composite 的封顶（44 < HOLD 线 45 → 落进 SELL/回避区，绝不给买入级结论）
SCREEN_VETO_CAP = 44.0
# 排雷 caution 时的温和扣分（不否决，但降级）
SCREEN_CAUTION_PENALTY = 8.0


def score_derivatives(snap: dict | None) -> tuple[float | None, dict]:
    """币安衍生品情绪 → 0-100（50 中性）。资金费率为主（反向），多空比辅助。

    - 资金费率正（多头付钱）= 多头拥挤过热 → 偏空；负（空头付钱）= 恐慌 → 波段常是更好进场点 → 偏多。
    - 多空账户比 >1（散户偏多）常做**反向**指标 → 略偏空。
    OI 仅入 detail 不打分（无趋势上下文难定向）。全空返回 (None, ...)。
    """
    if not snap:
        return None, {"available": False}
    from common.scoring_utils import clamp
    parts: list[float] = []
    detail: dict[str, Any] = {"available": True}

    funding = snap.get("funding") or {}
    fr = funding.get("funding_rate")
    if isinstance(fr, (int, float)):
        # 0.01%(0.0001) 中性附近；±0.1%(0.001) 视为过热/恐慌极值 → ±20 分
        comp = 50.0 - clamp(fr * 20000.0, -20.0, 20.0)
        parts.append(comp)
        detail["funding_rate"] = fr
        detail["funding_score"] = round(comp, 1)

    ls = snap.get("long_short") or {}
    ratio = ls.get("ratio")
    if isinstance(ratio, (int, float)) and ratio > 0:
        comp = 50.0 - clamp((ratio - 1.0) * 20.0, -15.0, 15.0)
        parts.append(comp)
        detail["long_short_ratio"] = ratio
        detail["long_short_score"] = round(comp, 1)

    oi = snap.get("open_interest") or {}
    if oi:
        detail["open_interest"] = oi.get("oi")

    if not parts:
        return None, {"available": False}
    return round(clamp(sum(parts) / len(parts), 0, 100), 1), detail


def score_regime(btc_regime: dict | None, market_ctx: dict | None) -> tuple[float | None, dict]:
    """大势闸门 → 0-100。BTC 200 日牛/熊定基调，恐慌贪婪微调（极端恐惧=机会/极端贪婪=风险）。

    90% 山寨是 BTC 影子：BTC 熊市显著压低做多分，是波段最重要的一道闸。
    """
    from common.scoring_utils import clamp
    reg = (btc_regime or {}).get("regime")
    if reg == "bull":
        base = 65.0
    elif reg == "bear":
        base = 30.0
    else:
        base = 50.0
    detail: dict[str, Any] = {"available": reg is not None, "btc_regime": reg,
                              "pct_from_ma": (btc_regime or {}).get("pct_from_ma")}

    fng = (market_ctx or {}).get("fear_greed") or {}
    v = fng.get("value")
    adj = 0.0
    if isinstance(v, (int, float)):
        if v < 25:
            adj = 8.0            # 极度恐惧：波段常是买点
        elif v >= 75:
            adj = -8.0           # 极度贪婪：过热风险
        else:
            adj = (45.0 - v) / 45.0 * 5.0   # 中间区温和线性
        detail["fear_greed"] = v
    detail["btc_dominance"] = (market_ctx or {}).get("btc_dominance")

    if reg is None and not isinstance(v, (int, float)):
        return None, {"available": False}
    return round(clamp(base + adj, 0, 100), 1), detail


def score_crypto_cockpit(dimensions: dict[str, float | None],
                         screen_result: dict | None = None,
                         dynamic_levels: dict | None = None,
                         current_position_pct: float = 0.0,
                         max_position_pct: float = 0.5,
                         quality: Any = None,
                         calibration_factor: float = 1.0) -> dict:
    """择时三维 + 排雷否决闸 → 综合分/动作/价位/目标仓位。镜像 `score_cockpit`。

    dimensions: {technical, derivatives, regime} → 0-100 或 None（缺维自动重归一）。
    screen_result: `screen_coin` 产出；verdict=='avoid' → composite 硬封顶到 SCREEN_VETO_CAP
        （否决闸）；'caution' → 温和扣分。
    quality/calibration_factor: 同 cockpit（数据质量硬钳 + 历史命中率校准，作用在硬钳前）。
    """
    from cockpit_engine.scorer import (
        CLAMP_CAP,
        _recommendation,
        _target_position_pct,
    )
    from common.scoring_utils import clamp

    available = {k: v for k, v in dimensions.items() if v is not None}
    if not available:
        return {
            "composite": None, "recommendation": "N/A",
            "suggested_position_pct": 0.0, "suggested_add_pct": 0.0,
            "current_position_pct": round(current_position_pct, 1),
            "stop_loss": None, "take_profit": None, "weights_used": {},
            "available_dimensions": [], "dimension_coverage": 0.0,
            "raw_composite": None, "calibration_factor": 1.0, "adjustments": (),
        }

    total_w = sum(CRYPTO_WEIGHTS[k] for k in available)
    weights_used = {k: round(CRYPTO_WEIGHTS[k] / total_w, 4) for k in available}
    composite = round(sum(available[k] * weights_used[k] for k in available), 1)
    dimension_coverage = round(total_w / sum(CRYPTO_WEIGHTS.values()), 4)
    raw_composite = composite
    adjustments: list[str] = []

    # 历史命中率校准（硬钳之前，只下调）
    if calibration_factor is not None and calibration_factor != 1.0:
        composite = round(composite * calibration_factor, 1)
        adjustments.append("confidence_calibrated_by_history")

    # 数据质量硬钳（核心数据降级 → 封顶 CLAMP_CAP）
    if quality is not None and getattr(quality, "core_degraded", False) and composite > CLAMP_CAP:
        composite = CLAMP_CAP
        adjustments.append("composite_capped_core_data_degraded")

    # ── 排雷否决闸（加密独有）──
    verdict = (screen_result or {}).get("verdict")
    if verdict == "avoid" and composite > SCREEN_VETO_CAP:
        composite = SCREEN_VETO_CAP
        adjustments.append("capped_by_screen_veto")
    elif verdict == "caution":
        composite = round(max(0.0, composite - SCREEN_CAUTION_PENALTY), 1)
        adjustments.append("screen_caution_penalty")

    composite = round(clamp(composite, 0, 100), 1)

    stop_loss = take_profit = None
    if isinstance(dynamic_levels, dict):
        stop_loss = dynamic_levels.get("atr_stop_loss") or dynamic_levels.get("trailing_stop")
        for lv in (dynamic_levels.get("take_profit_levels") or []):
            if isinstance(lv, dict) and lv.get("level") == 2:
                take_profit = lv.get("price")
                break

    target_pct = _target_position_pct(composite, max_position_pct)
    add_pct = round(max(0.0, target_pct - current_position_pct), 1) if target_pct > 0 else 0.0

    return {
        "composite": composite,
        "recommendation": _recommendation(composite),
        "suggested_position_pct": target_pct,
        "suggested_add_pct": add_pct,
        "current_position_pct": round(current_position_pct, 1),
        "stop_loss": stop_loss, "take_profit": take_profit,
        "weights_used": weights_used,
        "available_dimensions": list(available.keys()),
        "dimension_coverage": dimension_coverage,
        "raw_composite": raw_composite,
        "calibration_factor": calibration_factor,
        "adjustments": tuple(adjustments),
    }


def score_dimensions(md: dict, dev: dict) -> tuple[dict, int, list[str]]:
    """纯扣分逻辑（不出网，可离线测）：吃 CoinGecko market_data/developer_data 两个 dict，
    产 (dimensions, score 0-100, flags)。透明扣分制，主流币天然高分。"""
    circ = md.get("circulating_supply")
    max_sup = md.get("max_supply")
    mcap = (md.get("market_cap") or {}).get("usd")
    fdv = (md.get("fully_diluted_valuation") or {}).get("usd")
    commits_4w = dev.get("commit_count_4_weeks")

    dims = {
        "circulating_supply": circ, "max_supply": max_sup,
        "market_cap_usd": mcap, "fdv_usd": fdv,
        "fdv_mcap_ratio": round(fdv / mcap, 2) if (fdv and mcap) else None,
        "commits_4w": commits_4w, "stars": dev.get("stars"),
    }

    score = 100
    flags: list[str] = []

    # ① 供应上限（无上限 = 潜在通胀稀释）
    if max_sup is None:
        score -= 12
        flags.append("无供应上限（潜在通胀稀释）")

    # ② 解锁悬顶代理：FDV/市值比（远大于 1 = 大量代币未流通，未来解锁砸盘风险）
    ratio = dims["fdv_mcap_ratio"]
    if ratio is not None:
        if ratio >= 3:
            score -= 25
            flags.append(f"大量代币未流通（FDV/市值={ratio}，解锁砸盘风险高）")
        elif ratio >= 1.5:
            score -= 12
            flags.append(f"部分代币未流通（FDV/市值={ratio}，留意解锁）")

    # ③ 开发活跃度（4 周零提交 = 项目可能已死）
    if commits_4w is None:
        score -= 5
        flags.append("开发活跃度未知")
    elif commits_4w == 0:
        score -= 30
        flags.append("开发停滞（GitHub 近 4 周零提交）")
    elif commits_4w < 5:
        score -= 10
        flags.append(f"开发冷清（近 4 周仅 {commits_4w} 次提交）")

    # ④ 市值（小市值 = 流动性差/易被操纵）
    if mcap is not None:
        if mcap < 100_000_000:
            score -= 20
            flags.append("小市值（<1亿美元，流动性/操纵风险）")
        elif mcap < 1_000_000_000:
            score -= 8
            flags.append("中小市值（<10亿美元，波动更大）")

    return dims, max(0, min(100, score)), flags
