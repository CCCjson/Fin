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

from common.market import CRYPTO, label_of
from crypto_strategy.performance import (
    ARCHIVED_STATUSES,
    market_of,
    strategy_market,
)

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

class UnknownMarketError(ValueError):
    """传进来的市场认不出来。

    🔴 **不回落到默认市场**：`normalize_market` 对认不出的字符串一律回落 default
    （`?market=a_shre`、LLM 传「股票」都会**静默拿到 crypto 竞技场**），而有卫冕者
    那一支的 verdict 通篇不提市场名 —— AI 很容易把 crypto 的结论当成股票的答复
    报给 Jason。与本模块「口径可比」那道门槛同一个 fail-closed 口径。
    """


def resolve_market(raw: str | None) -> str:
    """外部传进来的市场字符串 → canonical，**认不出就抛**。

    ⚠️ 空/None 才回落 crypto（唯一在跑真钱的市场，也是老调用方的默认行为）。
    """
    from common.market import CANONICAL_MARKETS, normalize_market
    if raw is None or not str(raw).strip():
        return CRYPTO
    m = normalize_market(raw, default="")
    if m not in CANONICAL_MARKETS:
        raise UnknownMarketError(
            f"认不出市场「{raw}」。可选："
            f"{'、'.join(f'{label_of(x)}({x})' for x in CANONICAL_MARKETS)}")
    return m


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
    # 🔀 还要卡**同一个市场**（S5）：各市场的本金和收益都是不同币种 ——
    # 拿 A 股的 CNY 收益率和币的 USDT 收益率比大小是没有意义的。
    # ⚠️ 这只是**止血**：真正的「每个市场族内一个卫冕者」是 S5 任务 03 的事，
    #    它涉及改裁决 7 的读法，要 Jason 拍板。这里先保证**不会给出跨市场的错结论**。
    cb, mb = challenger.get("basis"), champion.get("basis")
    ccb, mcb = challenger.get("capital_basis"), champion.get("capital_basis")
    cm, mm = challenger.get("market"), champion.get("market")
    has_data = bool(cb) and cb != "none" and bool(mb) and mb != "none"
    same_mode = cb == mb
    same_denom = ccb == mcb
    # 🔒 **fail-closed**：取不到市场就不许放行。写成 `cm == mm` 的话，
    # 一个忘了带 `market` 的调用方会拿到 `None == None` → 静默判过 ——
    # 「取不到值 → 当作满足」正是 S5 一路踩过来的那个失败模式，这道门槛不带着它出生。
    same_market = bool(cm) and cm == mm
    gates["comparable"] = {
        "passed": has_data and same_mode and same_denom and same_market,
        "detail": (
            f"挑战者 {cb or '无数据'} vs 卫冕者 {mb or '无数据'} —— "
             f"至少有一边算不出收益序列，没法比。" if not has_data else
             f"挑战者 {cb} vs 卫冕者 {mb} —— paper 是理想撮合（挂单必成、零冲击），"
             f"**与实盘不可比**。要比就让挑战者也跑实盘，或两条都在 paper 里比。"
             if not same_mode else
             f"本金口径不同（{ccb} vs {mcb}）：一条按真实账户值定仓位、一条按配置本金，"
             f"收益率量级会整体偏一个倍数，跟策略好坏无关。" if not same_denom else
             f"市场不同（{label_of(cm)} vs {label_of(mm)}）：两边的本金和收益"
             f"都是不同币种，"
             f"比大小没有意义。同市场内才排名。" if not same_market else
             f"两边都是 {cb}、本金口径都是 {ccb}、都在{label_of(cm)}"),
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
        # ⚠️ market 取自**策略行**而不是序列返回值：算不出序列时（basis=none）
        # 也必须知道它是哪个市场的，否则「口径可比」那道门槛没得判。
        "market": strategy_market(row),
        "is_benchmark": bool(row.is_benchmark),
        "backtest_passed": bool(row.backtest_passed),
        "basis": dr.get("basis"), "returns": dr.get("returns") or [],
        "capital_basis": dr.get("capital_basis"),
        "days": dr.get("days") or 0, "trade_days": dr.get("trade_days") or 0,
        "trade_count": dr.get("trade_count") or 0,
        "open_positions": dr.get("open_positions") or {},
        # 序列的成色（股票才有）：`partial`/`suspected` 说明有几天是工作日启发式
        # 补出来的（不认识节假日）。⛔ 别让门槛② 拿一条掺了假期的序列当真。
        "calendar_confidence": dr.get("calendar_confidence"),
        "heuristic_days": dr.get("heuristic_days"),
        "no_data_reason": dr.get("reason"),
    }


def markets_with_strategies(*, include_archived: bool = False) -> list[str]:
    """哪些市场有策略 —— **一次纯 SQL**，不算任何战绩。**这个问题只准这一处回答。**

    ⭐ 存在的理由是让「还有别的市场」这件事能穿过接口边界：分族之后每个出口只看
    一个市场，不带这个字段的话，一旦股票也 arm 上 live，卫冕者面板只显示 crypto，
    而 `/standings` 是全市场混列的 —— **两块屏对不上**，而 Jason 无从知道该切哪个。

    🔴 **口径刻意与 `standings()` 对齐**（未归档的都算，含 draft/backtested），
    因为它防的就是「这块屏说有、那块屏说没有」。⛔ 别在调用处另写一套筛法：
    第一版就是这么分叉的 —— `evaluate_arena` 内联了一份按 `enabled==1` 筛的，
    于是同一个字段对 `/champion` 和 `/verdict` 给出**两个答案**，
    而分叉点恰好是第二个市场最早、最长的那个状态（刚编译完还没 arm）。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy
    session = get_session()
    try:
        q = session.query(CryptoStrategy.market)
        if not include_archived:
            # ⛔ 别写字面量：真源是 `performance.ARCHIVED_STATUSES`（`standings()` 用的
            # 就是它）。抄一份的话，往那边加一个归档状态时这里不会跟着变 ——
            # 「这块屏说有、那块屏说没有」会原样复发，只是下沉了一层。
            q = q.filter(CryptoStrategy.status.notin_(ARCHIVED_STATUSES))
        return sorted({market_of(m) for (m,) in q.all()})
    finally:
        session.close()


def snapshot_of(strategy_id: str, *, days: int = 90) -> dict[str, Any] | None:
    """按策略号取一份快照 —— **`_snapshot` 的唯一对外入口**。找不到策略返回 None。

    🔴 **⛔ 别再手拼第二份 snapshot dict。** 门槛全靠 `dict.get()` 读，漏一个键
    不会报错，只会**静默放行**：`market` 漏了就是 `None == None` → 「口径可比」判过 ——
    而留痕路径（`service._gates_snapshot`）恰恰靠它记「这次换对了吗」，
    于是最该留对的那一刻留下的是「四道门槛全过」。这个坑已经踩过一次。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy
    session = get_session()
    try:
        row = session.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == strategy_id).first()
        if row is None:
            return None
        session.expunge(row)
    finally:
        session.close()
    return _snapshot(row, days=days)


# 摘要里给 LLM/前端看的字段。⛔ 别把 `returns`（90+ 个 float × N 条）漏出去 ——
# 纯烧 token，而且它对「该不该换」这个问题一点解释力都没有。
_BRIEF = ("strategy_id", "name", "version", "mode", "status", "basis", "market",
          "capital_basis", "days", "trade_days", "trade_count",
          "calendar_confidence", "no_data_reason")


def _brief(s: dict[str, Any]) -> dict[str, Any]:
    out = {k: s.get(k) for k in _BRIEF}
    out["open_positions"] = len(s.get("open_positions") or {})
    return out


def _days_since_last_switch(market: str = CRYPTO) -> int | None:
    """距**这个市场**上一次切换多少天。None = 从没切换过（不受冷却期约束）。

    🔴 **刻意不按 family 查**：同市场同期只有一条 live，所以「上次换人」在一个市场里
    是**一件事**。按 family 查的话，从甲家族换到乙家族时查不到任何记录 → 冷却期永远
    None → 「防反复横跳」这道防线对**跨家族切换完全无效**，而那恰恰是最容易横跳的场景
    （AI 每次都能造一条全新策略）。

    🔀 **但必须按市场分**（裁决 7 新读法）：冷却期不分市场的话，换一次币策略会让 A 股
    14 天内换不了 —— 两个市场的横跳本来就是两件独立的事。

    ⚠️ `crypto_arena_switches` **没有 `market` 列**（加列要迁移，属于大改动）。
    所以按 `to_strategy_id` 反查策略行的市场。这张表**只记真的发生过的切换**（低频），
    两列全扫的代价可以忽略 —— ⛔ 别为了「优化」加 `LIMIT N`：某个市场的上次切换一旦
    排到 N 条之外，这里就会返回 None（=不受冷却期约束），**等于在历史最长的时候
    把防横跳关掉**，而且一声不吭。
    """
    from common.market_time import utc_now
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoArenaSwitch, CryptoStrategy
    session = get_session()
    try:
        # 🔒 **outerjoin 不是 join**：`delete_strategy()` 会硬删策略行，内连接会让
        # 指向它的切换记录**整条消失** → 冷却期回落到更早的一条、甚至变成 None
        # （=不受约束），而这正是「防反复横跳」最该生效的时候。
        # ⚠️ 一并取 `strategy_id` 当**存在性标记**：`market` 列自己分不清
        # 「join 不上」和「存量行 NULL（= crypto）」—— 两者都是 None。
        rows = (session.query(CryptoArenaSwitch.created_at,
                              CryptoStrategy.strategy_id, CryptoStrategy.market)
                .outerjoin(CryptoStrategy,
                           CryptoStrategy.strategy_id == CryptoArenaSwitch.to_strategy_id)
                .order_by(CryptoArenaSwitch.created_at.desc()).all())
    finally:
        session.close()
    for created_at, sid, raw_market in rows:
        if created_at is None:
            continue
        # ⚠️ 策略行被删了 = 市场未知 → **算进每个市场**，宁可多冷却一会儿。
        # 与「口径可比」那道门槛同一个 fail-closed 口径：取不到值不许放行。
        if sid is not None and market_of(raw_market) != market:
            continue
        return int(max(0, (utc_now() - created_at).days))
    return None


def current_champion(market: str = CRYPTO) -> dict[str, Any] | None:
    """**这个市场**当前跑实盘那条的身份牌 —— **一次纯 SQL，不算任何战绩**。

    ⭐ 存在的理由是省算力：`evaluate_arena` 会给**每条** enabled 策略跑一遍
    `daily_returns`（内含 `cost_basis.replay`）。调用方只想知道「谁在跑」时
    （比如交易终端的卫冕者面板要拿它去查体检报告），走全场判定等于白烧 O(N) 次回放。

    ⚠️ 筛选条件必须与 `evaluate_arena` **逐字一致**（含 `market` 这一条），
    否则两个出口会指向不同的策略。裁决 7 的新读法保证**每个市场**同期最多一条 live，
    所以不需要排序也不会有歧义；
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
            if (r.mode == "live" and r.status in _LIVE_STATUSES
                    and market_of(r.market) == market):
                return {
                    "strategy_id": str(r.strategy_id),
                    "name": r.name,
                    "version": r.version or 1,
                    "family_id": r.family_id or r.strategy_id,
                    "market": market,
                    "mode": r.mode,
                    "status": r.status,
                    "is_benchmark": bool(r.is_benchmark),
                    # 与 `evaluate_arena` 的 `champion_halted` 同一判据
                    "halted": r.status == "paused_by_guardrail",
                }
        return None
    finally:
        session.close()


def evaluate_arena(*, market: str = CRYPTO, days: int = 90,
                   cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """**一个市场**的全场判定：谁是卫冕者、谁在挑战、该不该换。

    卫冕者 = 这个市场当前跑实盘的那条（裁决 7 的新读法：**每个市场**同期只有一条 live，
    Jason 2026-08-03 拍板，见 `DECISIONS.md`）。

    ⚠️ **`market` 默认 crypto 是刻意的**：改成「一次返回所有市场」会把返回形状
    从「一个竞技场」变成「一堆竞技场」，而前端 `arenaService.ts` 和交易终端吃的是
    前者 —— 那是**另一件事**（UI 加市场切换器），不该混在判定层的改动里一起上。
    返回值带 `market` 和 `markets_with_strategies`，调用方看得见还有别的市场。

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
        # ⚠️ 市场在 **Python 里筛**（`market_of` 是「NULL = crypto」的唯一实现）——
        # 在 SQL 里手写 `market == 'crypto' OR market IS NULL` 就是第二份实现。
        # 在跑的策略只有个位数，多读几行远比口径分裂便宜。
        mine = [r for r in rows if market_of(r.market) == market]
        snaps = [_snapshot(r, days=days) for r in mine]
    finally:
        session.close()
    # ⛔ 别拿上面那批 `rows` 现算 —— 它按 `enabled==1` 筛，与 `/champion` 那边
    # 用的口径不同，同一个字段会给出两个答案（口径只准有一处）。
    markets = markets_with_strategies()

    champion = next((s for s in snaps
                     if s["mode"] == "live" and s["status"] in _LIVE_STATUSES), None)
    others = [s for s in snaps if s is not champion and s["enabled"]]
    benchmarks = [s for s in others if s["is_benchmark"]]
    # ⭐ 基准线不计入挑战者数 —— 否则它会替 AI 抬高多重比较的门槛。
    challengers = [s for s in others if not s["is_benchmark"]]

    if champion is None:
        return {"market": market, "markets_with_strategies": markets,
                "champion": None, "challengers": 0,
                "challenger_details": [_brief(s) for s in challengers],
                "benchmarks": [_brief(b) for b in benchmarks], "results": [],
                # ⚠️ 话要说成「**这个市场**没有卫冕者」：分族之后「没有卫冕者」不再等于
                # 「整个系统没在自动交易」，说笼统了会让人以为别的市场也停了。
                # ⚠️ 念给 Jason 听的句子一律用中文市场名（`label_of`）——
                #    屏幕上同时出现「加密」和「crypto」指同一个东西，是实测踩过的。
                "verdict": (f"{label_of(market)}这个市场现在没有在跑实盘的策略"
                            f"（没有卫冕者）——「该不该换」这个问题还不成立。"
                            f"先 arm 一条上 live。"
                            + (f"（其它有策略的市场："
                               f"{'、'.join(label_of(m) for m in markets if m != market)}）"
                               if [m for m in markets if m != market] else ""))}

    n = len(challengers)
    cooldown = _days_since_last_switch(market)
    results = []
    for ch in challengers:
        r = evaluate_challenger(ch, champion, challenger_count=n,
                                days_since_last_switch=cooldown, cfg=cfg)
        results.append({"challenger": _brief(ch), **r})

    winners = [r for r in results if r["should_switch"]]
    weak = champion_weakening(champion["returns"])
    return {
        "market": market,
        "markets_with_strategies": markets,
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
