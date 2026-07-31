"""加密货币回测包装器 —— 复用 C++ 信号驱动引擎，绕过「整数股 + 100 股一手」的 A 股假设。

## 为什么需要这层（改前必读）

C++ 回测引擎（唯一口径）全程用**整数股数量**，且 `external_signal_strategy` 买入
**向下取整到 100 股（A 股一手）**。这两条对加密货币直接失效：
  - BTC 单价 6 万+，$10 万本金连「100 股」都凑不齐 → **零成交**；
  - 加密是**小数币量**（0.0015 BTC），整数量粒度太粗（1 BTC = $64k，占 $10 万本金 64%）。

给 C++ 引擎加浮点数量是大手术（动 portfolio/engine/metrics/全部策略）。这里用
**价格缩放**这个经济等价变换绕过：把所有 bar 价格 × 一个常数 k（缩到 $10 量级），
本金不变。于是：
  - `qty = floor(cash×weight / (close×k) / lot)×lot` 变成大整数，取整误差可忽略；
    （⚠️ S8 起 crypto 的 lot 已从 100 改成 1，见 `MarketRules::crypto()`）
  - 收益率 = 盈亏 / 本金、夏普、回撤 **全部缩放不变**（都是比值）；
  - 手续费 = 成交额 × 费率，成交额 ≈ cash×weight **缩放不变**；加密无最低佣金/无印花税，
    故 `CommissionConfig::crypto()` 口径不被 min_commission 扭曲。

**返回口径**：收益率/夏普/回撤/胜率/费率**直接可用**；trades/equity 里的**绝对价格是缩放后的**
（乘回 1/k 才是真实币价，但回测决策看比值，通常不需要）。这是 v1 近似——若日后要精确的
绝对价位/持仓币量，再给 C++ 引擎上浮点数量。
"""
from typing import Any

from services.backtest_cpp_client import run_portfolio, run_signals

# 缩放目标：把窗口内价格的**中位数**缩到这个量级（$10），让整数股的粒度误差 <0.1%
_TARGET_PRICE = 10.0


def _pick_scale(bars: list[dict[str, Any]]) -> float:
    """选缩放因子 k，使窗口内的价格落在 `_TARGET_PRICE` 量级。空/异常回退 1.0。

    🔴 **按中位数取，不是按首根 close**（2026-07-31 改）。

    旧实现拿第一根 bar 定 k，两个后果都实测过：
    1. **一根脏的首 bar 能让整个币静默消失**：`close=1e-8` 打头 → k=1e9 →
       后续价格全变 6e13 → `afford()` 算出 0 股 → **一单不下、零异常、零日志**。
       在 10 个币的组合里，这个币悄悄贡献 0，而组合收益率看上去一切正常。
    2. 币在窗口内涨得越多，后期的 scaled price 越大、整数股取整误差越大 ——
       gate 拉 400 天，memecoin 涨 100 倍不是稀奇事。

    中位数对两种情形都稳：单根异常值影响不到它，涨幅也被摊到窗口中部。
    ⚠️ 它只是让误差**小**，不是消灭误差 —— 见 `run_crypto_portfolio_backtest`
    docstring 里关于「缩放不变只成立到取整粒度」的说明。
    """
    closes = sorted(float(b["close"]) for b in bars
                    if b.get("close") and float(b["close"]) > 0)
    if not closes:
        return 1.0
    mid = closes[len(closes) // 2]
    return _TARGET_PRICE / mid


def _scale_bars(bars: list[dict[str, Any]], k: float) -> list[dict[str, Any]]:
    out = []
    for b in bars:
        nb = dict(b)
        for f in ("open", "high", "low", "close"):
            if nb.get(f) is not None:
                nb[f] = float(nb[f]) * k
        out.append(nb)
    return out


def _scale_signal_prices(signals: list[dict[str, Any]], k: float) -> list[dict[str, Any]]:
    out = []
    for s in signals:
        ns = dict(s)
        if ns.get("price") is not None:
            ns["price"] = float(ns["price"]) * k
        out.append(ns)
    return out


def run_crypto_backtest(
    bars: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    initial_capital: float = 100000.0,
    symbol: str = "BTCUSDT.BN",
    start_date: str = "",
    end_date: str = "",
    slippage_pct: float | None = None,
    risk_config: dict | None = None,
) -> dict[str, Any]:
    """加密货币信号驱动回测。走 C++ `crypto` 费率（taker 0.1%、无印花税/无最低佣金）。

    bars/signals 与 `run_signals` 同格式（[{date,open,high,low,close,volume}] /
    [{date,action,price?,weight?}]）。内部做价格缩放，返回 C++ 原始结果 dict；
    `_price_scale` 键给出所用缩放因子（trades/equity 的绝对价格 = 结果值 / _price_scale）。
    """
    k = _pick_scale(bars)
    res = run_signals(
        _scale_bars(bars, k),
        _scale_signal_prices(signals, k),
        initial_capital=initial_capital,
        market="crypto",
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        slippage_pct=slippage_pct,
        risk_config=risk_config,
    )
    if isinstance(res, dict):
        res["_price_scale"] = k
    return res


def run_crypto_portfolio_backtest(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    signals_by_symbol: dict[str, list[dict[str, Any]]],
    initial_capital: float = 100000.0,
    start_date: str = "",
    end_date: str = "",
    slippage_pct: float | None = None,
    risk_config: dict | None = None,
) -> dict[str, Any]:
    """加密货币**组合**回测：N 个币共享同一份资金（S8）。

    ## ⭐ 逐币缩放为什么不会把共享账本弄乱

    单标的回测靠「价格 × k 缩到 $10 量级」绕开整数股粒度（见模块头）。
    组合回测里每个币的 k 都不同（BTC 的 k ≈ 1.5e-4，DOGE 的 ≈ 67），
    看上去像是把一份共享现金拿去跟一堆量纲不一的价格做加减。

    账本上参与加减的两个量在**数学上**确实与 k 无关：
    - 买入花的钱 `qty × (close×k) ≈ cash×w`（k 约掉）
    - 持仓市值 `qty × close_t × k ∝ close_t/close_0`（只反映这个币自己的涨跌）

    🔴 **但整数股取整会破坏它**，而且破坏得不小。实测：绕开 `_pick_scale`
    直接送两条相差 1000 倍的腿进去，组合收益从 **0.1968 变成 0.1729（12% 偏差）**
    —— 高价位那条腿一笔单只买得起个位数股。

    所以真正保证正确性的**不是**「不变性成立」，而是
    **`_pick_scale` 逐币把价格归一到同一量级，账本因此永远见不到混合量纲**。
    ⛔ 别把 `_pick_scale` 改成全场共用一个 k，也别去掉它。
    ⛔ 别把它改回「按首根 close」——暴涨的币在窗口后段会精度崩掉
       （极端情况 qty=0，整个币静默不成交）。中位数对这两种情形都稳。

    ⚠️ 被 k **污染**的是 `trades` / `equity_curve` 里的**绝对价格**
    （每个币要各自乘回 `1/k` 才是真实币价）—— 返回里逐币给出 `_price_scale`。
    ⛔ 别把不同币的 `trades[].price` 放在一起比大小，那是不同量纲的数。

    两条测试钉这件事（`tests/test_crypto_portfolio_backtest.py`）：
    `test_result_is_invariant_to_raw_price_magnitude`（价位换成 64000/0.0015
    收益不变）与 `test_pick_scale_keeps_every_leg_in_the_same_magnitude`。

    ## ⚠️ 一手 = 1 股（S8 起）

    C++ 侧 crypto 的一手已经从 100 股改成 1 股。这件事在组合回测里是**必须的**：
    资金被 N 个币摊薄后，「100 股一手」的取整误差会从 0.1% 放大到 5-10%
    （$10 万 1 个币 = 1000 手；摊到 10 个币只剩 10 手）。

    Args:
        bars_by_symbol: `{symbol: [{date, open, high, low, close, volume}, ...]}`
        signals_by_symbol: `{symbol: [{date, action, price?, weight?}, ...]}`
            ⚠️ 只给 bars 不给 signals 的币会当**行情背景**参与（不下单）。
            ⛔ 给了 signals 却没给 bars 的币直接报错 —— 静默丢掉会让
            「10 个币的组合」悄悄变成 3 个币，而收益率看上去一切正常。
        risk_config: 建议带 `max_total_position_pct`（0.8 = 留 20% 现金）。

    Returns:
        C++ 原始结果 + `_price_scale`（`{symbol: k}`，逐币）。
    """
    missing = sorted(set(signals_by_symbol) - set(bars_by_symbol))
    if missing:
        raise ValueError(
            f"这些币有信号却没有 bars：{missing}。"
            f"组合回测不会静默丢掉它们 —— 丢了的话「{len(bars_by_symbol) + len(missing)} "
            f"个币的组合」会悄悄变成 {len(bars_by_symbol)} 个币，而收益率看上去一切正常。")

    legs: list[dict[str, Any]] = []
    scales: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        k = _pick_scale(bars)
        scales[symbol] = k
        leg: dict[str, Any] = {"symbol": symbol, "bars": _scale_bars(bars, k)}
        sigs = signals_by_symbol.get(symbol)
        if sigs is not None:
            leg["signals"] = _scale_signal_prices(sigs, k)
        legs.append(leg)

    res = run_portfolio(
        legs,
        initial_capital=initial_capital,
        market="crypto",
        start_date=start_date,
        end_date=end_date,
        slippage_pct=slippage_pct,
        risk_config=risk_config,
    )
    if isinstance(res, dict):
        # ⚠️ 逐币一个 k（单标的版那个 `_price_scale` 是标量，别混用）
        res["_price_scale"] = scales
    return res
