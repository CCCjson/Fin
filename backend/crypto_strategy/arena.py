"""策略竞技场判定 —— 「该不该把卫冕者换掉」（S3）。

⛔ **裁决 10：规则判「切不切」，AI 判「为什么」和「下一条试什么」。**
四道门槛全是纯量化判定（比均值差、算标准误、数笔数、查冷却期），规则引擎几行就够；
**让 LLM 判反而更慢更贵还不稳定**（同样的数据可能给出不同答案）。
AI 该干的是：① 解释卫冕者为什么开始失效 ② 造新的挑战者
③ **对规则提出异议**（「数字上该切了，但这是财报季噪声，建议再观察两周」）。

# 四个必须防的坑（量化经典送命题，`00-PLAN §3` 裁决 9）

1. **paper 与 live 不可比** —— paper 是理想撮合（挂单必成、零冲击、零排队）。
   跨模式一律判「不可比」，⛔ 不是判「挑战者赢」。
2. **多重比较** —— 同时跑 10 条，最好的那条大概率是运气。挑战者越多，门槛越高。
3. **追逐近期最优 = 追涨杀跌的策略版** —— 换过去往往正是旧策略均值回归的起点。
   冷却期 + 观察期下限一起挡。
4. **收益率是错的指标** —— 会系统性选出高杠杆高波动的。所以比的是**日收益率序列的
   显著性**（自带波动惩罚），并单独卡最大回撤。⛔ 别在任何地方用「总收益率谁高」排名。

本模块**不写库、不改状态**：判定只是意见，真要换仍要走 `arm_crypto_strategy` 的确认门。
"""
from __future__ import annotations

import math
from typing import Any

# ── 默认阈值（`00-PLAN §7` 的 Jason 建议值，全部可配）──────────────────────
MIN_OBSERVE_DAYS = 30        # 门槛①：挑战者至少跑够多少天
MIN_TRADE_DAYS = 20          # 门槛①：至少多少个「有交易的日子」
SE_MULTIPLE = 1.5            # 门槛②：均值差要超过多少倍标准误
DRAWDOWN_TOLERANCE = 1.2     # 门槛④：挑战者回撤允许比卫冕者差多少倍
COOLDOWN_DAYS = 14           # 冷却期：距上次切换

# 标准误小于这个数就当「没有波动」。
# ⚠️ **不能写 `se == 0`**：`_var([0.01]*10)` 因为浮点残差不是 0 而是 ~3e-37，
# 于是 se ≈ 8e-20，任何正的均值差都能「显著」——一条日收益完全恒定的序列会被判成
# 无限显著，直接把门槛② 架空。日收益率是小数，1e-9 已经远低于任何有意义的波动。
_SE_EPS = 1e-9

# 「还在跑实盘」的状态集合。⚠️ 必须含 `paused_by_guardrail`：护栏熔断会把 `enabled`
# 置 0，只按 enabled 找卫冕者的话，**最该给判据的那一刻工具会说「没有卫冕者」**。
# 与 `crypto_strategy/service.py::_LIVE_STATUSES` 同一套口径，改一边要改另一边。
_LIVE_STATUSES = ("armed", "paused_by_guardrail")

GATE_LABELS = {
    "observation": "观察期够长",
    "significance": "优势显著",
    "backtest": "回测过闸",
    "risk": "风险不劣化",
    "cooldown": "过了冷却期",
    "comparable": "口径可比",
}


# ── 序列统计（纯函数）────────────────────────────────────────────────────

def max_drawdown(returns: list[float]) -> float:
    """按日收益率序列累乘出的最大回撤（正数，0.08 = 回撤 8%）。

    ⚠️ 用累乘不是累加：收益率是比例，-50% 之后 +50% 回不到原点。累加会把这件事抹平，
    正好抹掉门槛④ 想抓的那种「大起大落」。
    （区分用例：`[0.5, -0.5]` 累乘 = 0.5，累加 = 0.333。）

    ⚠️ `r <= -1` 直接判 100% 回撤并收尾：权益变成 0 或负数之后 `(peak-equity)/peak`
    这个式子就没有意义了（实测 `[-2.0]` 会算出 200% 回撤）。正常数据到不了这里，
    但分母被设成异常小值时到得了。
    """
    peak = 1.0
    equity = 1.0
    worst = 0.0
    for r in returns:
        if r <= -1.0:
            return 1.0                    # 本金归零，再往后算没有意义
        equity *= (1.0 + r)
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)   # peak ≥ 1.0，恒为正
    return round(worst, 6)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs: list[float]) -> float:
    """样本方差（n-1）。n<2 时返 0 —— 一个点算不出离散度。"""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def significance(challenger: list[float], champion: list[float],
                 *, challenger_count: int = 1,
                 se_multiple: float = SE_MULTIPLE) -> dict[str, Any]:
    """挑战者的日收益率是否**显著**高于卫冕者。

    用 Welch 式的两样本标准误（两条序列长度/波动都可能不同）。

    ⭐ **`challenger_count` 抬高门槛是防「多重比较」**：同时跑 N 条，
    最好的那条大概率是运气。这里用 Šidák 修正的粗糙版
    `k' = k × sqrt(1 + ln(N))` —— 不追求统计严谨（我们本来也没有真正的 p 值），
    追求的是「挑战者越多，线越高」这个**方向正确且说得清**的性质。
    """
    n1, n2 = len(challenger), len(champion)
    if n1 < 2 or n2 < 2:
        return {"passed": False, "reason": "样本点不足 2 个，算不出标准误",
                "diff": None, "se": None, "threshold": None}
    se = math.sqrt(_var(challenger) / n1 + _var(champion) / n2)
    diff = _mean(challenger) - _mean(champion)
    k = se_multiple * math.sqrt(1.0 + math.log(max(1, challenger_count)))
    if se < _SE_EPS:
        # 两条序列都毫无波动（例如全是 0，或每天都恰好一样）—— 没有波动就没有
        # 「显著」可言。放行的话等于说「它每天稳赚 0.01%，所以必然更好」，
        # 而那种序列多半是**数据太稀疏被补零补出来的**，不是真的稳。
        return {"passed": False, "reason": "两条序列都没有波动，谈不上显著性",
                "diff": round(diff, 8), "se": round(se, 12), "threshold": None,
                "se_multiple_effective": round(k, 3)}
    return {
        "passed": diff > k * se,
        "diff": round(diff, 8), "se": round(se, 8),
        "threshold": round(k * se, 8),
        "se_multiple_effective": round(k, 3),
        "challenger_count": challenger_count,
        "reason": (f"日均收益差 {diff:+.4%}，需超过 {k:.2f} 倍标准误"
                   f"（{k * se:.4%}）—— 同时有 {challenger_count} 个挑战者，"
                   f"门槛已按多重比较抬高"),
    }


# ── 四道门槛 ─────────────────────────────────────────────────────────────

def evaluate_challenger(challenger: dict[str, Any], champion: dict[str, Any], *,
                        challenger_count: int = 1,
                        days_since_last_switch: int | None = None,
                        cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """一个挑战者对上卫冕者，逐道门槛判。

    Args:
        challenger / champion: 每个是
            `{strategy_id, name, version, basis, returns, days, trade_days,
              backtest_passed, is_benchmark}`。
        days_since_last_switch: None = 从没切换过（不受冷却期约束）。

    Returns:
        `{should_switch, gates: {名字: {passed, detail}}, blocked_by: [...]}`
        —— `should_switch` 为真**当且仅当每一道都过**。
    """
    c = {**{"min_days": MIN_OBSERVE_DAYS, "min_trade_days": MIN_TRADE_DAYS,
            "se_multiple": SE_MULTIPLE, "dd_tolerance": DRAWDOWN_TOLERANCE,
            "cooldown_days": COOLDOWN_DAYS}, **(cfg or {})}
    gates: dict[str, dict[str, Any]] = {}

    # ⭐ 裁决 8：基准线不参与切换 —— 它是尺子，不是选手。
    if challenger.get("is_benchmark"):
        return _verdict(gates, extra="挑战者是基准线策略，**永远不参与切换**（它只当尺子）。")

    # 门槛 0：口径可比。paper 是理想撮合，跟 live 比大小是自欺（坑 1）。
    # ⚠️ 还要卡**分母同源**：一条按真实账户值定仓位、一条按配置本金，收益率量级会整体
    # 偏一个倍数，而那跟策略好坏毫无关系。
    cb, mb = challenger.get("basis"), champion.get("basis")
    ccb, mcb = challenger.get("capital_basis"), champion.get("capital_basis")
    has_data = bool(cb) and cb != "none" and bool(mb) and mb != "none"
    same_mode = cb == mb
    same_denom = ccb == mcb
    gates["comparable"] = {
        "passed": has_data and same_mode and same_denom,
        "detail": (
            f"挑战者 {cb or '无数据'} vs 卫冕者 {mb or '无数据'} —— "
             f"至少有一边算不出收益序列，没法比。" if not has_data else
             f"挑战者 {cb} vs 卫冕者 {mb} —— paper 是理想撮合（挂单必成、零冲击），"
             f"**与实盘不可比**。要比就让挑战者也跑实盘，或两条都在 paper 里比。"
             if not same_mode else
             f"本金口径不同（{ccb} vs {mcb}）：一条按真实账户值定仓位、一条按配置本金，"
             f"收益率量级会整体偏一个倍数，跟策略好坏无关。" if not same_denom else
             f"两边都是 {cb}、本金口径都是 {ccb}"),
    }

    # 门槛 ①：观察期。
    # 🔴 `days` 必须是「策略实际活了多久」而不是查询窗口长度；
    # 「≥N 笔」也必须数**成交笔数**而不是「有已实现盈亏的自然日」—— 持仓跨日在 crypto
    # 完全正常，按日数会让这道门槛事实上不可达。
    days = challenger.get("days") or 0
    trades = challenger.get("trade_count")
    if trades is None:
        trades = challenger.get("trade_days") or 0
    gates["observation"] = {
        "passed": days >= c["min_days"] and trades >= c["min_trade_days"],
        "detail": (f"跑了 {days} 天（需 ≥{c['min_days']}）、"
                   f"成交 {trades} 笔（需 ≥{c['min_trade_days']}）"),
    }

    # 门槛 ②：显著性（含多重比较修正）
    sig = significance(challenger.get("returns") or [], champion.get("returns") or [],
                       challenger_count=challenger_count, se_multiple=c["se_multiple"])
    gates["significance"] = {"passed": sig["passed"], "detail": sig.get("reason"), **sig}

    # 门槛 ③：回测过闸
    gates["backtest"] = {
        "passed": bool(challenger.get("backtest_passed")),
        "detail": "净费回测已通过" if challenger.get("backtest_passed") else "净费回测没过",
    }

    # 门槛 ④：风险不劣化（回撤）。⛔ 别用收益率排名，那会系统性选出高杠杆高波动的（坑 4）
    dd_c = max_drawdown(challenger.get("returns") or [])
    dd_m = max_drawdown(champion.get("returns") or [])
    limit = dd_m * c["dd_tolerance"]
    # 🔴 这道门槛**只看得见已实现盈亏**：序列里没有浮亏，所以一条「赢了就跑、亏了死扛」
    # 的策略在这里回撤是 0。必须把未平仓头寸摆出来，别让它假装看得见。
    open_c = challenger.get("open_positions") or {}
    open_m = champion.get("open_positions") or {}
    blind = ""
    if open_c or open_m:
        blind = (f" ⚠️ 这个数**只含已实现盈亏**：挑战者还挂着 {len(open_c)} 个未平仓头寸、"
                 f"卫冕者 {len(open_m)} 个，它们的浮亏不在里面 —— "
                 f"「赢了就跑、亏了死扛」的策略在这道门槛上看起来会很干净。")
    gates["risk"] = {
        "passed": dd_c <= limit,
        "detail": (f"挑战者最大回撤 {dd_c:.2%}，卫冕者 {dd_m:.2%}，"
                   f"容差上限 {limit:.2%}") + blind,
        "challenger_max_drawdown": dd_c, "champion_max_drawdown": dd_m,
        "challenger_open_positions": len(open_c),
        "champion_open_positions": len(open_m),
        "unrealized_blind_spot": bool(open_c or open_m),
    }

    # 冷却期：防反复横跳吃双份手续费（坑 3）
    if days_since_last_switch is None:
        gates["cooldown"] = {"passed": True, "detail": "还没切换过，不受冷却期约束"}
    else:
        gates["cooldown"] = {
            "passed": days_since_last_switch >= c["cooldown_days"],
            "detail": (f"距上次切换 {days_since_last_switch} 天"
                       f"（需 ≥{c['cooldown_days']}）"),
        }
    return _verdict(gates)


def _verdict(gates: dict[str, dict[str, Any]], *, extra: str | None = None) -> dict[str, Any]:
    blocked = [k for k, v in gates.items() if not v.get("passed")]
    should = bool(gates) and not blocked
    if extra:
        should = False
    # ⭐ 每道门槛自带中文名，**别让调用方各抄一份**：交易终端抄过一次，措辞立刻
    # 就和这里分叉了（「观察期够长」→「观察期」）。口径只准有一处。
    for k, v in gates.items():
        v.setdefault("label", GATE_LABELS.get(k, k))
    return {
        "should_switch": should,
        "gates": gates,
        "blocked_by": blocked,
        "blocked_labels": [GATE_LABELS.get(k, k) for k in blocked],
        "verdict": extra or _verdict_text(should, gates, blocked),
    }


def _verdict_text(should: bool, gates: dict, blocked: list[str]) -> str:
    """一句人话结论 —— 屏幕上任一数字，3 秒内说不出「所以我该干嘛」就不配摆出来
    （裁决 16）。
    """
    if not gates:
        return "没有可判定的挑战者。"
    if should:
        return ("四道门槛全过，**规则判定：可以换**。"
                "⚠️ 规则不知道行情背景 —— 换之前值得让 AI 说一句「有没有理由先别换」，"
                "而且上位之后建议先小仓位试跑一段（paper 的理想撮合会高估挑战者）。")
    first = blocked[0]
    label = GATE_LABELS.get(first, first)
    tail = {
        "comparable": "先把口径对齐，不然比的是两种不同的东西。",
        "observation": "样本还不够，现在换就是在赌运气。",
        "significance": "领先幅度还在噪声范围内 —— 这正是「追逐近期最优」最容易翻车的地方。",
        "backtest": "先让它过净费回测。",
        "risk": "赚得多但波动也更大，**这不算赢**。",
        "cooldown": "刚换过没多久，再换会吃双份手续费还容易反复横跳。",
    }.get(first, "")
    return f"**不换**：卡在「{label}」。{gates[first].get('detail')}。{tail}"


# ── 卫冕者变弱（独立触发，比换人更紧急 —— 裁决 11）────────────────────────

def champion_weakening(returns: list[float], *, recent_days: int = 14,
                       min_history: int = 30) -> dict[str, Any]:
    """卫冕者是不是**正在**变弱。

    ⭐ 这与「挑战者变强」是**两个独立触发**：后者走四道门槛慢慢来，前者一旦成立就该
    立刻处理，**哪怕没有挑战者接位**（退 paper 或空仓也比继续亏强）。

    ⛔ **只报警，不自动降级。** 自动降级 = 规则能停掉 Jason 的策略，越权了
    （对照 `00-PLAN §1.2` 第 3 条：AI/规则是研究助手，不是决策者）。
    护栏的硬熔断（当日回撤破 3%）仍然照常自动生效 —— 那是事后的、写死的红线；
    这里做的是**更早、更软**的趋势预警。
    """
    if recent_days < 1:
        # ⚠️ Python 切片陷阱：`returns[:-0]` 是 `[]` 不是「全部」，静默变成「样本太少」。
        return {"weakening": False, "reason": "recent_days 必须 ≥1"}
    if len(returns) < min_history:
        return {"weakening": False,
                "reason": f"历史不足 {min_history} 天，趋势判断没有意义"}
    recent = returns[-recent_days:]
    baseline = returns[:-recent_days]
    if len(baseline) < 2 or len(recent) < 2:
        return {"weakening": False, "reason": "窗口切完之后样本太少"}
    m_recent, m_base = _mean(recent), _mean(baseline)
    se = math.sqrt(_var(recent) / len(recent) + _var(baseline) / len(baseline))
    drop = m_base - m_recent
    # ⚠️ 用 `_SE_EPS` 不用 `> 0`，理由同 `significance` —— 浮点残差会让一条几乎恒定的
    # 序列产生 ~1e-20 的 se，于是 0.000001% 的下滑也被报成「变弱」。
    weakening = se > _SE_EPS and drop > SE_MULTIPLE * se
    return {
        "weakening": weakening,
        "recent_mean": round(m_recent, 8), "baseline_mean": round(m_base, 8),
        "drop": round(drop, 8), "se": round(se, 8),
        "recent_days": len(recent), "baseline_days": len(baseline),
        "reason": (f"最近 {len(recent)} 天日均 {m_recent:+.4%}，"
                   f"此前基线 {m_base:+.4%}，"
                   + ("下滑幅度已超噪声范围" if weakening else "下滑还在噪声范围内")),
        "note": ("⚠️ 只报警不自动降级 —— 要不要退 paper/空仓由 Jason 决定。"
                 "护栏的硬熔断（当日回撤破 3%）仍然照常自动生效。"),
    }


# ── 组装：从库里取数 → 判定（这一层碰 DB，上面全是纯函数）────────────────

def _snapshot(row: Any, *, days: int) -> dict[str, Any]:
    from crypto_strategy.performance import daily_returns
    dr = daily_returns(row.strategy_id, days=days)
    return {
        "strategy_id": row.strategy_id, "name": row.name,
        "version": row.version or 1, "family_id": row.family_id or row.strategy_id,
        "status": row.status, "mode": row.mode, "enabled": bool(row.enabled),
        "is_benchmark": bool(row.is_benchmark),
        "backtest_passed": bool(row.backtest_passed),
        "basis": dr.get("basis"), "returns": dr.get("returns") or [],
        "capital_basis": dr.get("capital_basis"),
        "days": dr.get("days") or 0, "trade_days": dr.get("trade_days") or 0,
        "trade_count": dr.get("trade_count") or 0,
        "open_positions": dr.get("open_positions") or {},
        "no_data_reason": dr.get("reason"),
    }


# 摘要里给 LLM/前端看的字段。⛔ 别把 `returns`（90+ 个 float × N 条）漏出去 ——
# 纯烧 token，而且它对「该不该换」这个问题一点解释力都没有。
_BRIEF = ("strategy_id", "name", "version", "mode", "status", "basis",
          "capital_basis", "days", "trade_days", "trade_count", "no_data_reason")


def _brief(s: dict[str, Any]) -> dict[str, Any]:
    out = {k: s.get(k) for k in _BRIEF}
    out["open_positions"] = len(s.get("open_positions") or {})
    return out


def _days_since_last_switch() -> int | None:
    """距**上一次任何切换**多少天。None = 从没切换过（不受冷却期约束）。

    🔴 **刻意不按 family 查**：同期只有一条 live（裁决 7），所以「上次换人」是**全局
    一件事**。按 family 查的话，从甲家族换到乙家族时查不到任何记录 → 冷却期永远 None →
    「防反复横跳」这道防线对**跨家族切换完全无效**，而那恰恰是最容易横跳的场景
    （AI 每次都能造一条全新策略）。
    """
    from common.market_time import utc_now
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoArenaSwitch
    session = get_session()
    try:
        row = (session.query(CryptoArenaSwitch)
               .order_by(CryptoArenaSwitch.created_at.desc()).first())
        if row is None or row.created_at is None:
            return None
        return int(max(0, (utc_now() - row.created_at).days))
    finally:
        session.close()


def current_champion() -> dict[str, Any] | None:
    """当前跑实盘那条的身份牌 —— **一次纯 SQL，不算任何战绩**。

    ⭐ 存在的理由是省算力：`evaluate_arena` 会给**每条** enabled 策略跑一遍
    `daily_returns`（内含 `cost_basis.replay`）。调用方只想知道「谁在跑」时
    （比如交易终端的卫冕者面板要拿它去查体检报告），走全场判定等于白烧 O(N) 次回放。

    ⚠️ 筛选条件必须与 `evaluate_arena` **逐字一致**，否则两个出口会指向不同的策略。
    裁决 7 保证同期最多一条 live，所以不需要排序也不会有歧义；
    `_LIVE_STATUSES` 含 `paused_by_guardrail` 的理由见 `evaluate_arena` 的坑 1。
    有门禁 `test_current_champion_agrees_with_evaluate_arena` 钉住这件事。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy

    session = get_session()
    try:
        rows = session.query(CryptoStrategy).filter(
            (CryptoStrategy.enabled == 1)
            | (CryptoStrategy.status == "paused_by_guardrail")).all()
        for r in rows:
            if r.mode == "live" and r.status in _LIVE_STATUSES:
                return {
                    "strategy_id": str(r.strategy_id),
                    "name": r.name,
                    "version": r.version or 1,
                    "family_id": r.family_id or r.strategy_id,
                    "mode": r.mode,
                    "status": r.status,
                    "is_benchmark": bool(r.is_benchmark),
                    # 与 `evaluate_arena` 的 `champion_halted` 同一判据
                    "halted": r.status == "paused_by_guardrail",
                }
        return None
    finally:
        session.close()


def evaluate_arena(*, days: int = 90, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """全场判定：谁是卫冕者、谁在挑战、该不该换。

    卫冕者 = 当前跑实盘的那条（裁决 7：同期只有一条 live）。

    🔴 **两个「谁是卫冕者」的坑，都踩过**：
    1. 只查 `enabled=1` → 卫冕者一被护栏熔断（`_halt` 会把 `enabled` 置 0）就变成
       「没有卫冕者」。**最该给判据的那一刻，工具装作无事发生**，连
       `champion_weakening` 都不算了 —— 直接顶掉裁决 11「卫冕者变弱更紧急」。
       所以按 `status in (armed, paused_by_guardrail) 且 mode=live` 找。
    2. 先把基准线剔出去再找 live → Jason 手写那条如果正在跑实盘（**最可能的情形**），
       同样会变成「没有卫冕者」。裁决 8 说的是基准线不当**挑战者**，没说它不能在跑。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy

    session = get_session()
    try:
        rows = session.query(CryptoStrategy).filter(
            (CryptoStrategy.enabled == 1)
            | (CryptoStrategy.status == "paused_by_guardrail")).all()
        snaps = [_snapshot(r, days=days) for r in rows]
    finally:
        session.close()

    champion = next((s for s in snaps
                     if s["mode"] == "live" and s["status"] in _LIVE_STATUSES), None)
    others = [s for s in snaps if s is not champion and s["enabled"]]
    benchmarks = [s for s in others if s["is_benchmark"]]
    # ⭐ 基准线不计入挑战者数 —— 否则它会替 AI 抬高多重比较的门槛。
    challengers = [s for s in others if not s["is_benchmark"]]

    if champion is None:
        return {"champion": None, "challengers": 0,
                "challenger_details": [_brief(s) for s in challengers],
                "benchmarks": [_brief(b) for b in benchmarks], "results": [],
                "verdict": ("现在没有在跑实盘的策略（没有卫冕者）——"
                            "「该不该换」这个问题还不成立。先 arm 一条上 live。")}

    n = len(challengers)
    cooldown = _days_since_last_switch()
    results = []
    for ch in challengers:
        r = evaluate_challenger(ch, champion, challenger_count=n,
                                days_since_last_switch=cooldown, cfg=cfg)
        results.append({"challenger": _brief(ch), **r})

    winners = [r for r in results if r["should_switch"]]
    weak = champion_weakening(champion["returns"])
    return {
        "champion": _brief(champion),
        "champion_halted": champion["status"] == "paused_by_guardrail",
        "champion_is_benchmark": champion["is_benchmark"],
        "champion_weakening": weak,
        "challengers": n,                         # 恒为 int，别让它有时变成 list
        "challenger_details": [_brief(s) for s in challengers],
        "benchmarks": [_brief(b) for b in benchmarks],
        "days_since_last_switch": cooldown,
        "results": results,
        "verdict": _arena_verdict(winners, results, weak, n, champion),
        "note": ("⚠️ 这只是**规则的意见**：本工具不写库、不改状态。真要换仍要走 "
                 "arm_crypto_strategy 的确认门。"),
    }


def _arena_verdict(winners: list, results: list, weak: dict, n: int,
                   champion: dict) -> str:
    parts = []
    if champion.get("status") == "paused_by_guardrail":
        # 卫冕者已经被护栏熔断停机 —— 这比任何「换不换」都优先。
        parts.append("🔴 **卫冕者已被护栏熔断停机**，现在实盘上没有策略在跑。"
                     "下面的对比是拿它停机前的数据算的。")
    if champion.get("is_benchmark"):
        parts.append("ℹ️ 当前在跑的是**基准线策略**（你手写那条）—— "
                     "换掉它意味着放弃这把尺子，值得多想一下。")
    if weak.get("weakening"):
        # 裁决 11：这条比「换不换人」更紧急，所以摆在最前面。
        parts.append(f"🔴 **卫冕者正在变弱**：{weak['reason']}。"
                     f"这件事比「换谁上」更紧急 —— 哪怕没有挑战者接位，"
                     f"退 paper 或空仓也比继续亏强（要不要退由你决定，规则不自动降级）。")
    if not n:
        parts.append("目前没有挑战者在跑。想比出高下，先 fork 一版丢进 paper 池观察。")
    elif winners:
        names = "、".join(f"{w['challenger']['name']} v{w['challenger']['version']}"
                          for w in winners)
        parts.append(f"✅ {len(winners)}/{n} 个挑战者过了全部门槛：{names}。"
                     f"规则判定可以换 —— 但换之前值得先问一句「有没有理由别换」。")
    else:
        # 把「大家都卡在同一道门槛」这件事说出来：那通常意味着门槛本身值得复核，
        # 而不是挑战者都不行。
        from collections import Counter
        first = Counter(r["blocked_by"][0] for r in results if r["blocked_by"])
        if first:
            top, cnt = first.most_common(1)[0]
            parts.append(f"暂时不换：{n} 个挑战者里 {cnt} 个卡在同一道门槛"
                         f"「{GATE_LABELS.get(top, top)}」。")
        else:
            parts.append("暂时不换。")
    return " ".join(parts)
