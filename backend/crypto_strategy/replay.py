"""DSL 逐日回放 —— 把 `crypto_metrics` 时序还原成「当天那一刻的分析卡」，喂给回测闸。

## 为什么需要它

回测闸此前只回放写死的双均线代理，**一条 DSL 条件都没被评估过**，却又把「已攒够历史」的
原语从 degraded 名单里扣除 —— 等于告诉 Jason「funding/排雷那几道闸验证过了」，而实际上
没有。这个模块补上真正的逐日评估。

## 怎么做到的（关键：evaluator 本来就是纯函数）

`crypto_strategy.evaluator.evaluate(group, analysis)` 吃的是一个 `analyze_crypto_symbol`
形状的 dict，且**字段缺失一律判不满足**。所以只要按 bar 日期造出「伪分析卡」——把当天的
指标值写进 `dsl.FIELD_RESOLVERS` 期望的那个嵌套位置 —— 就能逐日真评估，evaluator 一行不用改。

## 混合回放：代理只补缺失的那部分

`composite`/`dim.*`/新闻情绪这些聚合量没有历史序列，永远造不出帧。所以按**规则**决定形态：

- 该规则**含**非可回放原语 → `触发 = 双均线代理信号 AND 可回放条件全过`
  （代理代表那些评估不了的择时判断，可回放的那部分真的在 gate 交易）。
- 该规则**全部**原语可回放 → `触发 = 完整 DSL 评估`，**代理彻底退场**。

于是 `matured_fields` 名副其实：成熟一个原语，它就真的开始约束回测里的成交；全部成熟后
这就是一次忠实的 DSL 回放。对「条件全是 composite」的策略则退化成改动前的行为，向后兼容。

⚠️ `crypto_metrics` 2026-07-22 才开始攒，`_MIN_HISTORY_DAYS=60` → 上线初期成熟集合为空，
行为与改动前完全一致，随时序自然增强。
"""
from collections.abc import Callable
from typing import Any

from crypto_intel_engine.dsl import ConditionGroup, CryptoStrategySpec

# ──────────────────── 原语 ↔ 指标 ↔ 分析卡路径 ────────────────────

# DSL 原语 → `crypto_metrics` 里对应的 metric 名。这些量**每轮 tick 都在落时序**
# （见 `crypto_updater.refresh_intel` / `refresh_market_context`），所以只要库里攒够天数，
# 它们就从「无法回放」升级为「可回放」——degraded 名单会随时间自然变短。
METRIC_BACKED_FIELDS: dict[str, tuple[str, bool]] = {
    # field: (metric 名, 是否市场级 symbol='MARKET')
    "funding_rate": ("funding_rate", False),
    "long_short_ratio": ("long_short_ratio", False),
    "top_trader_ratio": ("top_trader_ratio", False),
    "taker_ratio": ("taker_ratio", False),
    "basis_rate": ("basis_rate", False),
    "screen.score": ("risk_score", False),
    "unlock_pct_30d": ("unlock_pct_30d", False),
    "fear_greed": ("fear_greed", True),
    "stablecoin_change_pct": ("stablecoin_supply", True),
}

# 原语 → 它在 `analyze_crypto_symbol` 结果里的嵌套路径。**必须与 `dsl.FIELD_RESOLVERS`
# 逐字段一致**，否则造出来的帧取不到值、条件静默判 False（整条闸假装没命中，最难查的那种）。
# 门禁 `test_replay_paths_match_field_resolvers` 用哨兵值把两边咬死，不靠人工同步。
REPLAY_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "funding_rate": ("derivatives_snapshot", "funding", "funding_rate"),
    "long_short_ratio": ("derivatives_snapshot", "long_short", "ratio"),
    "top_trader_ratio": ("derivatives_snapshot", "top_trader", "ratio"),
    "taker_ratio": ("derivatives_snapshot", "taker_flow", "ratio"),
    "basis_rate": ("derivatives_snapshot", "basis", "basis_rate"),
    "screen.score": ("screen", "score"),
    "unlock_pct_30d": ("screen", "unlock", "pct_of_supply"),
    "fear_greed": ("market_context", "fear_greed", "value"),
    "stablecoin_change_pct": ("dimensions", "flow", "detail", "stablecoin_change_pct"),
    "price.change_5d_pct": ("price", "change_5d_pct"),
    "price.change_20d_pct": ("price", "change_20d_pct"),
    "price.change_60d_pct": ("price", "change_60d_pct"),
}

# 价格类原语：不预造帧，逐 bar 用 history 切片现算（无未来函数，且与 cockpit 同口径）
PRICE_FIELD_PERIODS: dict[str, int] = {
    "price.change_5d_pct": 5, "price.change_20d_pct": 20, "price.change_60d_pct": 60,
}

# `stablecoin_change_pct` 落库的是**供应绝对值**，DSL 要的是变化 % —— 与
# `context.stablecoin_change_pct` 同口径（30 天）
_STABLECOIN_WINDOW = 30


def _set_path(frame: dict, path: tuple[str, ...], value: Any) -> None:
    """把 value 写进 frame 的嵌套路径（沿途缺的 dict 自动建）。"""
    cur = frame
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


# ──────────────────── 造帧 ────────────────────


def load_metric_maps(symbol: str, fields: set[str], *, days: int, session) -> dict[str, dict]:
    """读回放需要的那些原语的 {date_str: value}。只读 `fields` 里的，不做无用查询。

    `stablecoin_change_pct` 特殊：库里存的是供应绝对值，这里按 30 天窗口折算成变化 %，
    与 `context.stablecoin_change_pct` 口径一致（否则回测与实盘对同一条件的判断会分叉）。
    """
    from crypto_intel_engine.store import read_market_metric_map, read_metric_map

    out: dict[str, dict[str, float]] = {}
    for field in fields:
        spec = METRIC_BACKED_FIELDS.get(field)
        if not spec:
            continue
        metric, is_market = spec
        # 稳定币要多读一个窗口的历史，才能给最早那批 bar 算出变化率
        lookback = days + _STABLECOIN_WINDOW if field == "stablecoin_change_pct" else days
        raw = (read_market_metric_map(session, metric, days=lookback) if is_market
               else read_metric_map(session, symbol, metric, days=lookback))
        out[field] = _to_change_pct(raw) if field == "stablecoin_change_pct" else raw
    return out


def _to_change_pct(supply_by_date: dict[str, float]) -> dict[str, float]:
    """供应绝对值序列 → 每日的 30 天变化 %（前 30 个点算不出，直接不给值=条件判 False）。"""
    dates = sorted(supply_by_date)
    out: dict[str, float] = {}
    for i in range(_STABLECOIN_WINDOW, len(dates)):
        old = supply_by_date[dates[i - _STABLECOIN_WINDOW]]
        if not old:
            continue
        out[dates[i]] = round((supply_by_date[dates[i]] - old) / old * 100, 3)
    return out


def build_frames(dates: list[str], metric_maps: dict[str, dict]) -> dict[str, dict]:
    """{date_str: 伪分析卡}。当天没值的原语**不写进帧**（→ evaluator 判该条不满足）。"""
    frames: dict[str, dict] = {}
    for day in dates:
        frame: dict[str, Any] = {}
        for field, by_date in metric_maps.items():
            if day in by_date:
                _set_path(frame, REPLAY_FIELD_PATHS[field], by_date[day])
        frames[day] = frame
    return frames


def _price_frame(history) -> dict:
    """从 history 切片现算价格类原语（末根即当天，只看过去，无未来函数）。"""
    closes = history["close"]
    price: dict[str, Any] = {}
    for field, periods in PRICE_FIELD_PERIODS.items():
        if len(closes) <= periods:
            continue
        old = float(closes.iloc[-(periods + 1)])
        if not old:
            continue
        price[REPLAY_FIELD_PATHS[field][-1]] = round(
            (float(closes.iloc[-1]) - old) / old * 100, 2)
    return {"price": price} if price else {}


# ──────────────────── 条件树过滤 ────────────────────


def rule_fields(group: ConditionGroup) -> set[str]:
    """一条规则用到的全部原语名。"""
    return {c.field for c in list(group.all_of) + list(group.any_of)}


def filter_replayable(group: ConditionGroup, replayable: set[str]) -> ConditionGroup | None:
    """只保留可回放的条件。**一条都不剩时返回 None** = 本轮不施加 DSL 约束（全交给代理）。

    `any_of` 被剔空但 `all_of` 还有货时保持为空 —— `evaluate` 对空 `any_of` 视为通过，
    正是「这几条判断不了，别拿它阻断」的正确语义（评估不了 ≠ 不满足）。
    """
    all_of = [c for c in group.all_of if c.field in replayable]
    any_of = [c for c in group.any_of if c.field in replayable]
    if not all_of and not any_of:
        return None            # ConditionGroup 不允许全空，且语义上本来就是「无约束」
    return ConditionGroup(all_of=all_of, any_of=any_of)


# ──────────────────── on_bar 工厂 ────────────────────


def dsl_on_bar(spec: CryptoStrategySpec, frames: dict[str, dict], replayable: set[str],
               proxy_on_bar: Callable, *, date_of: Callable[[Any], str]) -> Callable:
    """产一个逐 bar 评估 DSL 的 on_bar（混合回放，形态见模块 docstring）。

    Args:
        frames: `build_frames` 的产物（date_str → 伪分析卡）。
        replayable: 本次真能逐日回放的原语集合。
        proxy_on_bar: 双均线代理，给「含非可回放原语」的规则当择时替身。
        date_of: history 末根 → date_str（与 frames 的键同口径）。
    """
    from crypto_strategy.evaluator import evaluate

    entry_all = rule_fields(spec.entry_rules.when)
    exit_all = rule_fields(spec.exit_rules.when)
    entry_needs_proxy = bool(entry_all - replayable)
    exit_needs_proxy = bool(exit_all - replayable)
    entry_group = (filter_replayable(spec.entry_rules.when, replayable) if entry_needs_proxy
                   else spec.entry_rules.when)
    exit_group = (filter_replayable(spec.exit_rules.when, replayable) if exit_needs_proxy
                  else spec.exit_rules.when)

    def _hit(group: ConditionGroup | None, frame: dict) -> bool:
        return True if group is None else evaluate(group, frame)[0]

    def on_bar(history) -> str | None:
        proxy = proxy_on_bar(history) if (entry_needs_proxy or exit_needs_proxy) else None
        frame = {**frames.get(date_of(history.index[-1]), {}), **_price_frame(history)}
        # 卖优先于买：同一根上两边都成立时先离场（与实盘引擎「持仓先看退出」一致）
        if _hit(exit_group, frame) and (proxy == "sell" or not exit_needs_proxy):
            return "sell"
        if _hit(entry_group, frame) and (proxy == "buy" or not entry_needs_proxy):
            return "buy"
        return None

    return on_bar
