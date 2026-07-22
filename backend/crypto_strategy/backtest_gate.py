"""上线前回测闸 —— live 武装的前置硬条件：净费回测必须为正。

## 保真边界（务必知情）

DSL 条件引用的是 `analyze_crypto_symbol` 的 cockpit 聚合量（composite/dim.*/资金费率/
排雷/大势/恐慌贪婪）。**价格/技术类**天然有历史序列；**资金费率/多空比/大户比/主动买卖比/
基差/排雷分/解锁占比/恐慌贪婪/稳定币供应**从 2026-07-22 起每轮 tick 落进 `crypto_metrics`，
攒够 `_MIN_HISTORY_DAYS` 天后由 `replayable_from_metrics()` 升格为「可回放」。

仍无历史的（composite/dim.* 这些聚合量、新闻情绪、4h 对齐）依旧标 degraded。

## 「可回放」是真的被回放了（不是嘴上说说）

⛔ 此前 `matured_fields` 只是把原语从 degraded 名单里**扣除**，回测却依旧只跑写死的双均线
代理——一条 DSL 条件都没被评估过，等于跟 Jason 说「funding/排雷那几道闸验证过了」而实际没有。
现在由 `crypto_strategy.replay` 逐日造帧真评估，形态按**规则**分：

- 规则里**还有**非可回放原语 → `触发 = 双均线代理 AND 可回放条件全过`
  （代理替那些评估不了的择时判断出面，可回放的那部分真的在 gate 交易）。
- 规则原语**全部**成熟 → `触发 = 完整 DSL 评估`，代理退场，`degraded=False`。

所以 `degraded` 不再写死 True：它 = `bool(degraded_reasons)`。返回值里的 `replay` 段
（mode/evaluated_fields/proxy_used）与 per_symbol 的 `replay_days` 让「到底回放了多少」可核对。

无论哪种形态，回测都**套用本策略自己的费率+滑点**（`CostModel`），所以「毛赚净亏/手续费
吃光」这个 Jason 最担心的失败模式始终被验证到。
"""
from collections.abc import Callable
from typing import Any

from crypto_intel_engine.backtest import run_crypto_backtest
from crypto_intel_engine.dsl import CryptoStrategySpec, round_trip_cost
from crypto_strategy.replay import METRIC_BACKED_FIELDS

# 回测代理只回放价格/技术类；其余原语无历史序列，用到即标 degraded
_REPLAYABLE_PREFIXES = ("price.",)
_REPLAYABLE_FIELDS = {"price.change_5d_pct", "price.change_20d_pct", "price.change_60d_pct"}

# 少于这么多天的历史，不算「可回放」（几个点算不出有意义的回测）
_MIN_HISTORY_DAYS = 60


def replayable_from_metrics(symbols: list[str], *, min_days: int = _MIN_HISTORY_DAYS,
                            session=None) -> set[str]:
    """查库：哪些 metric 支撑的原语**已经攒够历史**，可以逐日回放了。

    对每个候选原语，要求 universe 里**每个**币（市场级指标只查一次）都有 ≥ `min_days`
    条历史——只要有一个币缺，这条原语在本策略上就还不能算忠实回放。
    """
    from crypto_intel_engine.store import read_market_series, read_metric_series

    own_session = session is None
    if own_session:
        from data_engine.storage.database import get_session
        session = get_session()
    ready: set[str] = set()
    try:
        for field, (metric, is_market) in METRIC_BACKED_FIELDS.items():
            try:
                if is_market:
                    ok = len(read_market_series(session, metric, days=min_days * 2)) >= min_days
                else:
                    ok = all(
                        len(read_metric_series(session, s, metric, days=min_days * 2)) >= min_days
                        for s in symbols
                    )
            except Exception:  # noqa: BLE001 — 查不到就当没攒够，保守
                ok = False
            if ok:
                ready.add(field)
    finally:
        if own_session:
            session.close()
    return ready


_FAST, _SLOW = 10, 30    # 双均线代理周期（自然日）


def _ma_cross_on_bar(fast: int = _FAST, slow: int = _SLOW) -> Callable:
    """返回一个只看历史的 on_bar：短均线上穿长均线→buy，下穿→sell（无未来函数）。"""
    def on_bar(history) -> str | None:
        if len(history) < slow + 1:
            return None
        closes = history["close"]
        fast_now = closes.iloc[-fast:].mean()
        slow_now = closes.iloc[-slow:].mean()
        fast_prev = closes.iloc[-fast - 1:-1].mean()
        slow_prev = closes.iloc[-slow - 1:-1].mean()
        if fast_prev <= slow_prev and fast_now > slow_now:
            return "buy"
        if fast_prev >= slow_prev and fast_now < slow_now:
            return "sell"
        return None
    return on_bar


def _non_replayable_fields(spec: CryptoStrategySpec,
                           extra_replayable: set[str] | None = None) -> list[str]:
    """收集策略用到的、回测代理无法逐日回放的原语（去重）。

    `extra_replayable` 是「库里已攒够历史」的原语集合（见 `replayable_from_metrics`）——
    随着 `crypto_metrics` 时序变长，这个名单会自然缩短。
    """
    replayable = _REPLAYABLE_FIELDS | (extra_replayable or set())
    fields: set[str] = set()
    for rules in (spec.entry_rules, spec.exit_rules):
        for c in list(rules.when.all_of) + list(rules.when.any_of):
            if c.field not in replayable and not c.field.startswith(_REPLAYABLE_PREFIXES):
                fields.add(c.field)
    return sorted(fields)


# C++ 引擎对 market="crypto" 写死的 taker 费率（`CommissionConfig::crypto()`）。
# 接口不接受费率覆盖，故策略 CostModel 的 taker_fee_pct 传不下去 —— 差异如实报出来。
_ENGINE_TAKER_PCT = 0.001
# 回放覆盖度低于此值就提醒：net_return 被大量「结构上不可能开仓」的日子稀释了
_MIN_REPLAY_COVERAGE = 0.5


def run_backtest_gate(
    spec: CryptoStrategySpec,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    initial_capital: float = 100000.0,
    run_bt: Callable = run_crypto_backtest,
    collect: Callable | None = None,
    _matured: set[str] | None = None,
) -> dict[str, Any]:
    """对 universe 每个币逐日回放 DSL（成熟原语真评估 + 代理补缺），聚合净费回报。

    返回 {passed, net_return, metrics, degraded, degraded_reasons, replay, per_symbol}。
    passed = 平均净费回报 > 0。run_bt/collect/_matured 可注入以便单测（避开 C++/大数据/查库）。
    """
    if collect is None:
        from alpha_lab.signal_runner import collect_signals
        collect = collect_signals

    import pandas as pd

    from alpha_lab.signal_runner import _date_str
    from crypto_strategy.replay import build_frames, dsl_on_bar, load_metric_maps, rule_fields

    cm = spec.cost_model
    proxy_on_bar = _ma_cross_on_bar()
    # C++ 引擎侧强制止损（AI/DSL 的 on_bar 无状态）；滑点走策略假设
    risk_config = {"enabled": True, "stop_loss_pct": 0.08}
    per_symbol: list[dict[str, Any]] = []
    returns: list[float] = []

    # 库里已攒够历史的原语算「可回放」——**必须先算**，它决定 on_bar 的形态（代理 vs 真 DSL）
    if _matured is not None:
        matured = set(_matured)
    else:
        try:
            matured = replayable_from_metrics(list(bars_by_symbol))
        except Exception:  # noqa: BLE001 — 查库失败就当一条没攒够（保守，不放宽闸门）
            matured = set()
    replayable = _REPLAYABLE_FIELDS | matured
    used_fields = rule_fields(spec.entry_rules.when) | rule_fields(spec.exit_rules.when)
    evaluated = sorted(used_fields & replayable)
    proxy_used = bool(used_fields - replayable)

    for symbol, bars in bars_by_symbol.items():
        if not bars or len(bars) < _SLOW + 2:
            per_symbol.append({"symbol": symbol, "skipped": "历史不足", "net_return": None})
            continue
        df = pd.DataFrame(bars)
        if "date" in df.columns:
            df = df.set_index("date")
        dates = [_date_str(idx) for idx in df.index]
        frames = _frames_for(symbol, dates, used_fields & matured,
                             load_metric_maps, build_frames)
        on_bar = dsl_on_bar(spec, frames, replayable, proxy_on_bar, date_of=_date_str)
        signals = collect(on_bar, df)
        res = run_bt(bars, signals, initial_capital=initial_capital, symbol=symbol,
                     slippage_pct=cm.slippage_pct, risk_config=risk_config)
        metrics = (res or {}).get("metrics", res or {})
        net = _safe(metrics.get("total_return"))
        per_symbol.append({"symbol": symbol, "net_return": net,
                           "num_trades": metrics.get("total_trades") or metrics.get("num_trades"),
                           "sharpe": _safe(metrics.get("sharpe_ratio")),
                           # 这个币有几天真的取到了指标帧（0 = 全靠代理）
                           "replay_days": sum(1 for f in frames.values() if f)})
        if net is not None:
            returns.append(net)

    net_return = round(sum(returns) / len(returns), 6) if returns else None
    degraded_fields = _non_replayable_fields(spec, matured)
    mode = "proxy" if proxy_used and not evaluated else ("hybrid" if proxy_used else "dsl")
    # `degraded` 专指「你的规则没能被逐日回放」（原语不可回放 → 用了双均线代理）。
    # 下面两条是**另一类**问题：规则回放了，但这个 net_return 的含义比看上去弱。
    # 混进 degraded_reasons 会让两种完全不同的警告纠缠在一起，故单列 `caveats`。
    caveats: list[str] = []

    # ① 费率口径：C++ 引擎按 market="crypto" 用**标准 taker 0.1%**，接口不接受费率覆盖，
    #    策略 CostModel 里配的 taker_fee_pct **传不下去**（滑点能传，费率不能）。
    #    改这个要动 C++ 引擎重编译，超出本次范围——但必须如实说明，别让「按你的费率回测过了」
    #    这句话骗人。BNB 抵扣（0.075%）或 VIP 费率与 0.1% 有差时，真实净收益有系统性偏差。
    fee_basis = {"engine_taker_pct": _ENGINE_TAKER_PCT,
                 "strategy_taker_pct": cm.taker_fee_pct,
                 "matches": abs(cm.taker_fee_pct - _ENGINE_TAKER_PCT) < 1e-9}
    if not fee_basis["matches"]:
        caveats.append(
            f"回测按引擎标准 taker {_ENGINE_TAKER_PCT:.3%} 计费，而策略配置的是 "
            f"{cm.taker_fee_pct:.3%}（引擎接口不支持费率覆盖）——净收益有系统性偏差")

    # ② 回放覆盖度：原语「成熟」后 net_return 会**静默换口径**。指标只从 crypto_metrics
    #    开始攒（几十天），而 bars 拉 400 天：没有指标帧的那些日子里 evaluate 对缺值一律
    #    判 False → 结构上不可能开仓，却照样按全窗口平均算进 net_return。
    #    同一个数字在原语成熟前后含义完全不同，必须把「几天真有指标」摆出来。
    coverage = _replay_coverage(per_symbol, bars_by_symbol)
    if coverage is not None and coverage < _MIN_REPLAY_COVERAGE and evaluated:
        caveats.append(
            f"只有 {coverage:.0%} 的回测日有真实指标帧，其余日子进场条件结构上恒为 False"
            f"（指标历史还没攒够）——net_return 被大量「不可能开仓」的日子稀释，仅供参考")

    return {
        "passed": bool(net_return is not None and net_return > 0),
        "net_return": net_return,
        "metrics": {"avg_net_return": net_return, "symbols_tested": len(returns),
                    "round_trip_cost": round(round_trip_cost(cm), 6),
                    "matured_fields": sorted(matured),
                    "fee_basis": fee_basis, "replay_coverage": coverage},
        "degraded": bool(degraded_fields),
        "degraded_reasons": _degraded_reasons(mode, degraded_fields, evaluated),
        "caveats": caveats,     # 「数字本身可信度」的警告，与 degraded 正交
        "replay": {"mode": mode, "proxy_used": proxy_used, "evaluated_fields": evaluated},
        "per_symbol": per_symbol,
    }


def _replay_coverage(per_symbol: list[dict], bars_by_symbol: dict) -> float | None:
    """「有真实指标帧的天数 / 回测总天数」。没有可比对的币返回 None。"""
    have = total = 0
    for r in per_symbol:
        bars = bars_by_symbol.get(r.get("symbol")) or []
        if r.get("skipped") or not bars:
            continue
        have += int(r.get("replay_days") or 0)
        total += len(bars)
    return round(have / total, 4) if total else None


def _frames_for(symbol: str, dates: list[str], fields: set[str],
                load_metric_maps: Callable, build_frames: Callable) -> dict[str, dict]:
    """取该币的逐日指标帧。查库失败 → 空帧（退化成纯代理，绝不放宽闸门）。"""
    if not fields:
        return {}
    from data_engine.storage.database import get_session
    session = get_session()
    try:
        maps = load_metric_maps(symbol, fields, days=len(dates) + 5, session=session)
    except Exception:  # noqa: BLE001
        return {}
    finally:
        session.close()
    return build_frames(dates, maps)


def _degraded_reasons(mode: str, degraded_fields: list[str], evaluated: list[str]) -> list[str]:
    """三态如实交代：全代理 / 混合 / 纯 DSL 忠实回放。编译工具会把这段原样回给 Jason。"""
    if mode == "dsl":
        return []
    reasons = []
    if degraded_fields:
        reasons.append(f"未逐日回放的 DSL 原语：{degraded_fields}")
    if evaluated:
        reasons.append(f"已逐日真实评估的原语：{evaluated}")
    reasons.append("其余条件用双均线技术代理替身，验证该币在真实费率下的成本可行性"
                   if evaluated else
                   "回测用双均线技术代理，仅验证该币在真实费率下的成本可行性，非 DSL 忠实回放")
    return reasons


def _safe(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None   # NaN 过滤
    except (TypeError, ValueError):
        return None
