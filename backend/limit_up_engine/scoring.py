"""
规则打分模型 —— 给 candidate_pool.py 选出的候选股打分（MVP 不做 ML，见方案「预测层」）。

总分 = 0.35×动能强度 + 0.20×资金强度 + 0.20×题材热度 + 0.15×大盘情绪 + 0.10×强势延续

这是第一版经验权重/分档阈值，不是回测优化的结果——运行首月只看候选池内的相对排序，
不要迷信具体分数的绝对含义（见方案「预测层」表格与说明）。
"""
from typing import Dict, List, Optional

from common.limit_rules import get_limit_threshold

WEIGHTS = {
    "momentum": 0.35,
    "capital": 0.20,
    "theme": 0.20,
    "sentiment": 0.15,
    "continuation": 0.10,
}

_SELECT_REASON_SCORE = {
    "60日新高且近期多次涨停": 95,
    "近期多次涨停": 85,
    "60日新高": 65,
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _lerp(x: Optional[float], x0: float, x1: float, y0: float, y1: float) -> float:
    if x is None:
        return (y0 + y1) / 2
    if x1 == x0:
        return y0
    t = _clamp((x - x0) / (x1 - x0), 0.0, 1.0)
    return y0 + t * (y1 - y0)


def _approach_ratio(candidate: Dict) -> Optional[float]:
    ratio = candidate.get("approach_ratio")
    if ratio is not None:
        return ratio
    threshold = candidate.get("limit_threshold") or get_limit_threshold(candidate["symbol"], candidate.get("name"))
    change_pct = candidate.get("change_pct")
    if not threshold or change_pct is None:
        return None
    return change_pct / threshold


def _approach_score(ratio: Optional[float]) -> float:
    if ratio is None:
        return 30.0
    if ratio < 0.5:
        return _lerp(ratio, 0.0, 0.5, 0.0, 30.0)
    if ratio < 0.8:
        return _lerp(ratio, 0.5, 0.8, 30.0, 70.0)
    return _lerp(ratio, 0.8, 0.99, 70.0, 95.0)


def _volume_ratio_score(vr: Optional[float]) -> float:
    if vr is None:
        return 50.0  # 数据缺失（continuation/quasi 来源目前拿不到量比）给中性分，不因缺数据而扣分
    if vr < 1:
        return _lerp(vr, 0.0, 1.0, 0.0, 30.0)
    if vr <= 3:
        return _lerp(vr, 1.0, 3.0, 30.0, 90.0)
    return _lerp(vr, 3.0, 5.0, 90.0, 100.0)


def momentum_score(candidate: Dict) -> Dict:
    ratio = _approach_ratio(candidate)
    approach = _approach_score(ratio)
    vr_score = _volume_ratio_score(candidate.get("volume_ratio"))
    return {"score": 0.6 * approach + 0.4 * vr_score, "approach_ratio": ratio}


def _turnover_score(t: Optional[float]) -> float:
    if t is None:
        return 50.0
    if t < 3:
        return _lerp(t, 0.0, 3.0, 0.0, 40.0)
    if t < 5:
        return _lerp(t, 3.0, 5.0, 40.0, 80.0)
    if t <= 15:
        return 100.0 - abs(t - 10.0) / 5.0 * 20.0  # 5~15 区间"甜区"，10附近最高，两端衰减到80
    if t <= 25:
        return _lerp(t, 15.0, 25.0, 80.0, 40.0)
    return _clamp(_lerp(t, 25.0, 35.0, 40.0, 0.0), 0.0, 40.0)


def capital_score(candidate: Dict) -> float:
    turnover = _turnover_score(candidate.get("turnover"))
    amount = candidate.get("amount")
    mv = candidate.get("circulating_mv")
    amount_mv_pct = (amount / mv * 100) if amount and mv and mv > 0 else None
    amount_score = _turnover_score(amount_mv_pct)  # 量纲与换手率同级，复用同一条映射曲线
    return 0.5 * turnover + 0.5 * amount_score


def theme_score(candidate: Dict, industry_heat: Dict[str, int], zt_total: int) -> Dict:
    industry = candidate.get("industry")
    if not industry or not zt_total:
        return {"score": 50.0, "industry": industry, "count": None}  # 缺行业标签(quasi来源已知局限)给中性分
    count = industry_heat.get(industry, 0)
    ratio = count / zt_total
    return {"score": _lerp(ratio, 0.0, 0.25, 0.0, 100.0), "industry": industry, "count": count}


def sentiment_score(market_sentiment: Dict) -> float:
    zt_count = market_sentiment.get("limit_up_count") or 0
    break_rate = market_sentiment.get("break_rate") or 0.0
    profit_effect = market_sentiment.get("profit_effect") or {}
    avg_change = profit_effect.get("avg_change_pct") or 0.0

    if zt_count < 30:
        family_score = _lerp(zt_count, 0, 30, 0, 40)
    elif zt_count <= 60:
        family_score = _lerp(zt_count, 30, 60, 40, 70)
    else:
        family_score = _lerp(zt_count, 60, 120, 70, 100)

    break_score = 100.0 - _clamp(break_rate * 100 * 2, 0.0, 100.0)
    profit_score = _clamp(50.0 + avg_change * 10.0, 0.0, 100.0)

    return 0.4 * family_score + 0.3 * break_score + 0.3 * profit_score


def continuation_score(candidate: Dict) -> float:
    source = candidate.get("source")
    if source == "strong":
        return _SELECT_REASON_SCORE.get(candidate.get("select_reason"), 50.0)
    if source == "continuation":
        boards = candidate.get("consecutive_boards") or 0
        if boards <= 0:
            return 40.0
        if boards <= 7:
            return _lerp(boards, 1, 7, 50.0, 90.0)
        return 85.0  # 过热衰减：7板以上不再加分，反映高位滞涨风险
    return 50.0  # quasi：无历史强势背景，给中性基础分


def _build_reasons(
    candidate: Dict, momentum: Dict, theme: Dict, market_sentiment: Dict,
) -> List[Dict]:
    reasons = []

    ratio = momentum["approach_ratio"]
    threshold = candidate.get("limit_threshold") or get_limit_threshold(candidate["symbol"], candidate.get("name"))
    change_pct = candidate.get("change_pct")
    momentum_detail = f"涨幅{change_pct:.1f}%" if change_pct is not None else "涨幅数据缺失"
    if ratio is not None and threshold:
        momentum_detail += f"，已达该股涨停阈值({threshold:.1f}%)的{ratio*100:.0f}%"
    if candidate.get("volume_ratio") is not None:
        momentum_detail += f"，量比{candidate['volume_ratio']:.1f}倍"
    reasons.append({"indicator": "动能强度", "detail": momentum_detail})

    turnover = candidate.get("turnover")
    capital_detail = f"换手率{turnover:.1f}%" if turnover is not None else "换手率数据缺失"
    amount, mv = candidate.get("amount"), candidate.get("circulating_mv")
    if amount and mv:
        capital_detail += f"，成交额/流通市值比{amount/mv*100:.1f}%"
    reasons.append({"indicator": "资金强度", "detail": capital_detail})

    if theme["industry"]:
        theme_detail = f"所属{theme['industry']}行业，今日该行业{theme['count']}只涨停"
    else:
        theme_detail = "行业数据暂缺（该候选来自盘中扫描，未匹配到行业标签）"
    reasons.append({"indicator": "题材热度", "detail": theme_detail})

    zt_count = market_sentiment.get("limit_up_count")
    break_rate = market_sentiment.get("break_rate")
    avg_change = (market_sentiment.get("profit_effect") or {}).get("avg_change_pct")
    sentiment_detail = f"今日全市场涨停{zt_count}家"
    if break_rate is not None:
        sentiment_detail += f"，炸板率{break_rate*100:.0f}%"
    if avg_change is not None:
        sentiment_detail += f"，昨日涨停股今日平均{avg_change:+.1f}%"
    reasons.append({"indicator": "大盘情绪", "detail": sentiment_detail})

    source = candidate.get("source")
    if source == "strong":
        cont_detail = f"强势股池入选理由「{candidate.get('select_reason') or '未知'}」"
    elif source == "continuation":
        boards = candidate.get("consecutive_boards") or 0
        cont_detail = f"昨日{boards}连板，今日涨幅{change_pct:.1f}%仍未回落" if change_pct is not None else f"昨日{boards}连板，今日仍强势"
    else:
        cont_detail = "无历史强势背景，来自盘中准涨停扫描（自建规则筛出，非官方榜单）"
    reasons.append({"indicator": "强势延续", "detail": cont_detail})

    return reasons


def score_candidates(candidates: List[Dict], market_sentiment: Dict, industry_heat: Dict[str, int]) -> List[Dict]:
    """给候选股逐一打分，返回按 score 降序排列、带 rank 的结果列表。"""
    zt_total = market_sentiment.get("limit_up_count") or 0
    sentiment = sentiment_score(market_sentiment)

    scored = []
    for candidate in candidates:
        momentum = momentum_score(candidate)
        capital = capital_score(candidate)
        theme = theme_score(candidate, industry_heat, zt_total)
        continuation = continuation_score(candidate)

        total = (
            WEIGHTS["momentum"] * momentum["score"]
            + WEIGHTS["capital"] * capital
            + WEIGHTS["theme"] * theme["score"]
            + WEIGHTS["sentiment"] * sentiment
            + WEIGHTS["continuation"] * continuation
        )

        scored.append({
            "symbol": candidate["symbol"],
            "name": candidate.get("name"),
            "source": candidate.get("source"),
            "score": round(total / 100.0, 4),
            "momentum_score": round(momentum["score"], 1),
            "capital_score": round(capital, 1),
            "theme_score": round(theme["score"], 1),
            "sentiment_score": round(sentiment, 1),
            "continuation_score": round(continuation, 1),
            "reasons": _build_reasons(candidate, momentum, theme, market_sentiment),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    for i, item in enumerate(scored, start=1):
        item["rank"] = i
    return scored
