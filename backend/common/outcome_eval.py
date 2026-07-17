"""AI 建议的后验评估内核 —— 「MoneyBill 上周说买茅台，后来对了吗」。

**别和 C++ 回测混淆**：C++ 回测测的是**策略**（「金叉买入这条规则历史上赚不赚钱」）；
本模块测的是 **AI 的嘴**（「上周那条建议对了吗」）。两根轴，不冲突、不重复。

设计上是**纯逻辑、DB 无关、无时间副作用**：入参走 Protocol（`DecisionLog` 行和
`DailyQuote` 行天然结构匹配，零适配器直接传），唯一的时间概念是显式的 `age_days`
入参而不是 `date.today()` —— 所以整个模块可以零 fixture、零 mock、零 DB 地测。
放 `common/` 是因为分层铁律 `engines → acquisition → common/net`：只有最底层能
同时被 `decision_log.py` 和各引擎 import 而不产生反向依赖。

三条来自 `docs/14.具体实现计划/P0-1-decision-outcome.md` 的硬设计（已裁决，别改）：

1. **`unable` ≠ `miss`** —— 100 条建议 60 对 20 错 20 条根本没法评。把「没法评」
   算成「判错」→ 准确率 60%；真实是 60/80 = **75%**。差 15 个点全是自己冤枉自己。
2. **`engine_version` 戳** —— 判定口径（窗口/中性带）以后一定会改，不打版本戳
   历史结果会随代码演进**悄悄漂移**，而且你永远不知道。
3. **`first_hit="ambiguous"`** —— 同一根日线里止损止盈都被触及时，日线数据**无法**
   判断先后。必须显式标 ambiguous，**不许猜**。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Sequence

# 判定口径的版本戳。**改窗口天数 / 中性带 / ambiguous 策略 / 方向公式 = 必须 bump。**
# 不 bump 就等于让新口径的结果和旧口径的结果混在一张表里，跨版本混算胜率 ——
# 正是上方设计点 2 要防的事。
ENGINE_VERSION = "decision-outcome-v1"

# 中性带：|收益率| <= 1% 视为「没看错也没看对」。
# 取 1.0 是为了和 `analysis_engine/signal_tracker.py` 的 outcome 判定对齐（那里也是 ±1%），
# 全项目同一把尺子。（外部蓝本 daily_stock_analysis 用的是 2.0，我们不跟 —— 内部一致优先。）
NEUTRAL_BAND_PCT = 1.0

# 评估窗口：5 日看短期方向，20 日看中期。20 也是「评完了」的门槛。
HORIZONS = (5, 20)
MAX_WINDOW = 20

# 最少要几根 bar 才值得评。少于这个数连 5 日窗口都凑不齐。
MIN_BARS = 5

# 超过这个天龄还凑不满 bar，就从「可重试」降级为「不可重试」——
# 三个月都没补上的行情，明天也不会有（退市/长期停牌），别让它每天被扫一遍。
STALE_AFTER_DAYS = 90

# 能评出方向的 action。HOLD / AGGREGATE / cockpit 的 "N/A" 都不在此列 ——
# 它们没有可证伪的方向断言，**这不是「判错」，是「没法评」**。
_DIRECTIONAL_ACTIONS = frozenset({"BUY", "SELL"})

# 「明天补了数据重跑就可能评出来」的 unable。回填只会重扫这些。
#
# ⚠️ 与外部蓝本的一处**刻意分歧**：它把 `missing_anchor_price`（≈ 我们的
# `no_entry_price`）也算可重试，因为它的锚定价是**现拉行情**、可能只是暂时缺。
# 我们的 `entry_price` 是决策产生时就写死的存量字段，且被 `_IMMUTABLE_REFRESH_FIELDS`
# 冻结（原始决策不可篡改）—— **NULL 就永远是 NULL**，重试一万次也一样。所以它在
# 我们这儿是不可重试的。
RETRYABLE_UNABLE_REASONS = frozenset({
    "no_quotes",         # 一根 bar 都没有：停牌 / 新股 / 行情还没回补
    "insufficient_bars", # 有 bar 但不够 MIN_BARS：建议刚发出来，等几天就够了
})

# 不可重试（列在这儿只为可读，判定一律走 is_retryable）：
#   no_action              —— 建议根本没记 action（advisor 现在全是这个）
#   action_not_directional —— HOLD / AGGREGATE / "N/A"，没有可证伪的方向
#   no_entry_price         —— 没记入场价，见上方分歧说明
#   invalid_entry_price    —— 入场价 <= 0，脏数据
#   stale_no_data          —— 超过 STALE_AFTER_DAYS 仍无数据


class BarLike(Protocol):
    """一根日线。`DailyQuote` 直接满足。"""
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]


class AdviceLike(Protocol):
    """一条建议。`DecisionLog` 直接满足。"""
    action: Optional[str]
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]


@dataclass(frozen=True)
class OutcomeResult:
    """评估产出。字段名与 `DecisionLog` 的 outcome 列一一对应，便于整体 setattr。"""
    outcome_status: str                       # completed | pending | unable
    unable_reason: Optional[str] = None
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    outcome_5d: Optional[str] = None          # win | loss | neutral
    outcome_20d: Optional[str] = None
    hit_stop: Optional[int] = None            # 0/1；None = 建议里没写止损位，无从判起
    hit_target: Optional[int] = None
    first_hit: Optional[str] = None           # stop_loss | take_profit | ambiguous | none
    first_hit_days: Optional[int] = None
    engine_version: str = ENGINE_VERSION


def is_retryable(reason: Optional[str]) -> bool:
    """这条 unable 明天补了数据还值得再评吗？

    可重试性是 `unable_reason` 的**函数**，不是独立事实 —— 所以它不是一个数据库列。
    独立列会允许「reason=no_action 但 retryable=1」这种不可能状态存在。
    """
    return reason in RETRYABLE_UNABLE_REASONS


def _label(ret: Optional[float]) -> Optional[str]:
    """收益率 → win/loss/neutral。

    中性带**在评估期定死**、由 `ENGINE_VERSION` 背书，不能挪到查询期动态算 ——
    否则以后调带宽会让历史结果悄悄漂移（设计点 2）。
    """
    if ret is None:
        return None
    if ret > NEUTRAL_BAND_PCT:
        return "win"
    if ret < -NEUTRAL_BAND_PCT:
        return "loss"
    return "neutral"


def evaluate_single(
    advice: AdviceLike,
    bars: Sequence[BarLike],
    *,
    age_days: int = 0,
) -> OutcomeResult:
    """评一条建议。

    Args:
        advice: 一条建议。只读 action / entry_price / stop_loss / take_profit。
        bars: **建议日之后**的日线，升序，调用方已切好（与
            `signal_tracker._update_tracking(tracking, quotes)` 的契约逐字一致）。
            建议当天不算 —— 建议是当天收盘后给的，当天的 bar 已经是过去了。
        age_days: 建议距今天几天。**内核里唯一的时间概念**，显式传入而不是
            `date.today()`，这样整个函数是纯的、可测的。只用来把「数据暂缺」
            升级成「永远缺」。

    Returns:
        `OutcomeResult`。`completed` 的门槛是**方向可评**（action + entry_price +
        够 bar），**不是「止损止盈齐全」** —— `recommend_engine` 构造建议时压根没有
        take_profit 键，照「没写止损止盈就 unable」会让主力 source 100% unable。
    """
    stale = age_days > STALE_AFTER_DAYS

    # ---- 判定顺序即优先级，短路，不可换 ----
    # （顺序的意义：action=None 且 entry_price=None 时，答案必须是 no_action ——
    #   「连方向都没记」是更根本的原因，报 no_entry_price 会误导人去补价格。）
    action = (advice.action or "").strip().upper()
    if not action:
        return OutcomeResult("unable", "no_action")
    if action not in _DIRECTIONAL_ACTIONS:
        return OutcomeResult("unable", "action_not_directional")

    entry = advice.entry_price
    if entry is None:
        return OutcomeResult("unable", "no_entry_price")
    if entry <= 0:
        return OutcomeResult("unable", "invalid_entry_price")

    if not bars:
        return OutcomeResult("unable", "stale_no_data" if stale else "no_quotes")
    if len(bars) < MIN_BARS:
        return OutcomeResult("unable", "stale_no_data" if stale else "insufficient_bars")

    is_buy = action == "BUY"
    window = list(bars[:MAX_WINDOW])

    # ---- 各窗口收益率 + 标签 ----
    # 符号已按方向归一：**SELL 说跌、后来真跌了 → 正收益 → win。**
    # 这不是 bug，别「修」。归一之后 win_rate / avg_return 才能跨 BUY/SELL 混算。
    # 公式与 signal_tracker.py:146-152 逐字一致（全项目同一把尺子）。
    rets: Dict[int, Optional[float]] = {}
    for n in HORIZONS:
        if len(window) >= n:
            close = window[n - 1].close
            if close is None or close <= 0:
                rets[n] = None
                continue
            ret = (close / entry - 1) * 100 if is_buy else (1 - close / entry) * 100
            rets[n] = round(ret, 2)
        else:
            # 天数不够**显式写 None**，不是跳过 —— 同 signal_tracker.py:160。
            rets[n] = None

    # ---- 止损/止盈首次命中 ----
    stop = advice.stop_loss
    target = advice.take_profit

    # None（不是 0）表示「建议里没这个价位，无从判起」，与 0（扫过了、没碰到）区分。
    # 这样 hit_stop_rate 的分母才能只数真有止损位的行。照抄外部蓝本的
    # `hit_sl = None if stop_loss is None else False`，也照抄 signal_tracker.py:250
    # 那段 `stop_loss.isnot(None)` 的分母口径。
    hit_stop: Optional[int] = None if stop is None else 0
    hit_target: Optional[int] = None if target is None else 0
    first_hit: Optional[str] = None if (stop is None and target is None) else "none"
    first_hit_days: Optional[int] = None

    for i, bar in enumerate(window, start=1):
        low, high = bar.low, bar.high
        if is_buy:
            touch_stop = stop is not None and low is not None and low <= stop
            touch_target = target is not None and high is not None and high >= target
        else:
            # 做空：价格涨到止损位、跌到止盈位
            touch_stop = stop is not None and high is not None and high >= stop
            touch_target = target is not None and low is not None and low <= target

        if not touch_stop and not touch_target:
            continue

        # **首次命中就收工**（照蓝本 backtest_engine.py:736-767 的 break）。
        # 语义是「假如真按这条建议执行了，结果是什么」—— 一旦止损出局你已经空仓，
        # 后面价格再碰到止盈位跟你没关系。记全窗口会让 hit_target_rate 虚高：
        # 把「被扫出局之后的止盈」也算成打中目标，读起来像赚了其实没赚。
        hit_stop = 1 if touch_stop else hit_stop
        hit_target = 1 if touch_target else hit_target
        first_hit_days = i
        if touch_stop and touch_target:
            # 同一根日线里两个都碰到了 —— **日线数据无法判断先后，不许猜**。
            # 保守口径（按先止损算）不落在这儿，落在 compute_summary 的
            # stop_first_rate：**存的是观测事实，算的才是口径**。这样口径变了
            # bump ENGINE_VERSION 就行，不用回头改数据。
            first_hit = "ambiguous"
        elif touch_stop:
            first_hit = "stop_loss"
        else:
            first_hit = "take_profit"
        break

    # 20 根 bar 齐了才算评完；不齐则 pending，明天再来（outcome_5d 已经填好了）。
    status = "completed" if len(window) >= MAX_WINDOW else "pending"

    return OutcomeResult(
        outcome_status=status,
        return_5d=rets.get(5),
        return_20d=rets.get(20),
        outcome_5d=_label(rets.get(5)),
        outcome_20d=_label(rets.get(20)),
        hit_stop=hit_stop,
        hit_target=hit_target,
        first_hit=first_hit,
        first_hit_days=first_hit_days,
    )


def compute_summary(results: Sequence[OutcomeResult], *, horizon: int = 20) -> Dict[str, Any]:
    """一批评估结果 → 汇总指标（纯内存版，给回测和单测用）。

    线上查询走 `decision_log.get_decision_stats` 的 SQL 聚合版，**两者口径必须一致**。
    """
    total = len(results)
    evaluated = [r for r in results if r.outcome_status == "completed"]
    unable = [r for r in results if r.outcome_status == "unable"]

    breakdown: Dict[str, int] = {}
    for r in unable:
        key = r.unable_reason or "unknown"
        breakdown[key] = breakdown.get(key, 0) + 1

    attr = f"outcome_{horizon}d"
    ret_attr = f"return_{horizon}d"
    labels = [getattr(r, attr) for r in evaluated]
    wins = labels.count("win")
    losses = labels.count("loss")
    neutral = labels.count("neutral")
    judged = wins + losses + neutral

    rets = [getattr(r, ret_attr) for r in evaluated if getattr(r, ret_attr) is not None]

    # first_hit 分布：只数**真有价位可判**的行（first_hit is None = 建议里没写价位）
    hits = [r.first_hit for r in evaluated if r.first_hit is not None]
    fh: Dict[str, int] = {k: hits.count(k) for k in ("stop_loss", "take_profit", "ambiguous", "none")}
    decided = fh["stop_loss"] + fh["take_profit"] + fh["ambiguous"]

    days = [r.first_hit_days for r in evaluated if r.first_hit_days is not None]

    return {
        "engine_version": sorted({r.engine_version for r in results}) or [ENGINE_VERSION],
        "horizon_days": horizon,
        "total": total,
        "evaluated": len(evaluated),
        "pending": sum(1 for r in results if r.outcome_status == "pending"),
        "unable": len(unable),
        "unable_breakdown": breakdown,
        "win": wins,
        "loss": losses,
        "neutral": neutral,
        # **分母是 judged（可评的），不是 total。** 这是整张卡的题眼：
        # 60/100 = 60% 是自己冤枉自己，60/80 = 75% 才是真的。
        # judged == 0 → None 而不是 0.0：「一条都没法评」和「胜率 0%」是两回事，
        # 返 0.0 会让 MoneyBill 当着 Jason 的面说「advisor 历史胜率 0%」。
        "win_rate": round(wins / judged * 100, 2) if judged else None,
        "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
        "first_hit": fh,
        # **ambiguous 算进止损** —— 这就是「保守假设先止损」的落地点（口径，非事实）。
        "stop_first_rate": round((fh["stop_loss"] + fh["ambiguous"]) / decided * 100, 2) if decided else None,
        "avg_first_hit_days": round(sum(days) / len(days), 2) if days else None,
    }
