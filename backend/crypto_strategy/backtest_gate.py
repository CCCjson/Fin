"""上线前回测闸 —— live 武装的前置硬条件：净费回测必须为正。

## 诚实的 v1 保真边界（务必知情）

DSL 条件引用的是 `analyze_crypto_symbol` 的 cockpit 聚合量（composite/dim.*/资金费率/
排雷/大势/恐慌贪婪），其中**只有价格/技术类有历史序列**，资金费率/排雷/大势快照是
点时数据、库里无逐日历史 → **无法逐日忠实回放整条 DSL**。

因此 v1 回测 = **成本可行性代理**：用标准技术信号（双均线交叉）在该币历史上跑一遍，
**套用本策略自己的费率+滑点**（`CostModel`），回答「这个币按技术信号交易、在你假设的
成本下是否净赚」。这精确命中 Jason 最担心的「毛赚净亏/手续费吃光」失败模式，但**不等于**
验证了你 DSL 里 funding/排雷/大势那几条闸——那几条只在 **live 执行时忠实生效**。

结果一律标 `degraded=True` + 列出未被回放验证的原语，编译工具会把这段如实回给 Jason。
后续要精确回放，需把资金费率/排雷历史落库（阶段外）。
"""
from collections.abc import Callable
from typing import Any

from crypto_intel_engine.backtest import run_crypto_backtest
from crypto_intel_engine.dsl import CryptoStrategySpec, round_trip_cost

# 回测代理只回放价格/技术类；其余原语无历史序列，用到即标 degraded
_REPLAYABLE_PREFIXES = ("price.",)
_REPLAYABLE_FIELDS = {"price.change_5d_pct", "price.change_20d_pct", "price.change_60d_pct"}

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


def _non_replayable_fields(spec: CryptoStrategySpec) -> list[str]:
    """收集策略用到的、回测代理无法逐日回放的原语（去重）。"""
    fields: set[str] = set()
    for rules in (spec.entry_rules, spec.exit_rules):
        for c in list(rules.when.all_of) + list(rules.when.any_of):
            if c.field not in _REPLAYABLE_FIELDS and not c.field.startswith(_REPLAYABLE_PREFIXES):
                fields.add(c.field)
    return sorted(fields)


def run_backtest_gate(
    spec: CryptoStrategySpec,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    initial_capital: float = 100000.0,
    run_bt: Callable = run_crypto_backtest,
    collect: Callable | None = None,
) -> dict[str, Any]:
    """对 universe 每个币跑技术代理回测（套本策略费率+滑点），聚合净费回报。

    返回 {passed, net_return, metrics, degraded, degraded_reasons, per_symbol}。
    passed = 平均净费回报 > 0。run_bt/collect 可注入以便单测（避开 C++/大数据）。
    """
    if collect is None:
        from alpha_lab.signal_runner import collect_signals
        collect = collect_signals

    import pandas as pd

    cm = spec.cost_model
    on_bar = _ma_cross_on_bar()
    # C++ 引擎侧强制止损（AI/DSL 的 on_bar 无状态）；滑点走策略假设
    risk_config = {"enabled": True, "stop_loss_pct": 0.08}
    per_symbol: list[dict[str, Any]] = []
    returns: list[float] = []

    for symbol, bars in bars_by_symbol.items():
        if not bars or len(bars) < _SLOW + 2:
            per_symbol.append({"symbol": symbol, "skipped": "历史不足", "net_return": None})
            continue
        df = pd.DataFrame(bars)
        if "date" in df.columns:
            df = df.set_index("date")
        signals = collect(on_bar, df)
        res = run_bt(bars, signals, initial_capital=initial_capital, symbol=symbol,
                     slippage_pct=cm.slippage_pct, risk_config=risk_config)
        metrics = (res or {}).get("metrics", res or {})
        net = _safe(metrics.get("total_return"))
        per_symbol.append({"symbol": symbol, "net_return": net,
                           "num_trades": metrics.get("total_trades") or metrics.get("num_trades"),
                           "sharpe": _safe(metrics.get("sharpe_ratio"))})
        if net is not None:
            returns.append(net)

    net_return = round(sum(returns) / len(returns), 6) if returns else None
    degraded_reasons = _non_replayable_fields(spec)
    return {
        "passed": bool(net_return is not None and net_return > 0),
        "net_return": net_return,
        "metrics": {"avg_net_return": net_return, "symbols_tested": len(returns),
                    "round_trip_cost": round(round_trip_cost(cm), 6)},
        "degraded": True,
        "degraded_reasons": (
            [f"未逐日回放的 DSL 原语：{degraded_reasons}"] if degraded_reasons else []
        ) + ["回测用双均线技术代理，仅验证该币在真实费率下的成本可行性，非 DSL 忠实回放"],
        "per_symbol": per_symbol,
    }


def _safe(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None   # NaN 过滤
    except (TypeError, ValueError):
        return None
