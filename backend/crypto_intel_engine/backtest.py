"""加密货币回测包装器 —— 复用 C++ 信号驱动引擎，绕过「整数股 + 100 股一手」的 A 股假设。

## 为什么需要这层（改前必读）

C++ 回测引擎（唯一口径）全程用**整数股数量**，且 `external_signal_strategy` 买入
**向下取整到 100 股（A 股一手）**。这两条对加密货币直接失效：
  - BTC 单价 6 万+，$10 万本金连「100 股」都凑不齐 → **零成交**；
  - 加密是**小数币量**（0.0015 BTC），整数量粒度太粗（1 BTC = $64k，占 $10 万本金 64%）。

给 C++ 引擎加浮点数量是大手术（动 portfolio/engine/metrics/全部策略）。这里用
**价格缩放**这个经济等价变换绕过：把所有 bar 价格 × 一个常数 k（缩到 $10 量级），
本金不变。于是：
  - `qty = floor(cash×weight / (close×k) / 100)×100` 变成大整数，100 股一手误差可忽略；
  - 收益率 = 盈亏 / 本金、夏普、回撤 **全部缩放不变**（都是比值）；
  - 手续费 = 成交额 × 费率，成交额 ≈ cash×weight **缩放不变**；加密无最低佣金/无印花税，
    故 `CommissionConfig::crypto()` 口径不被 min_commission 扭曲。

**返回口径**：收益率/夏普/回撤/胜率/费率**直接可用**；trades/equity 里的**绝对价格是缩放后的**
（乘回 1/k 才是真实币价，但回测决策看比值，通常不需要）。这是 v1 近似——若日后要精确的
绝对价位/持仓币量，再给 C++ 引擎上浮点数量。
"""
from typing import Any

from services.backtest_cpp_client import run_signals

# 缩放目标：把首根 close 缩到这个量级（$10），让「100 股一手」的粒度误差 <0.1%
_TARGET_PRICE = 10.0


def _pick_scale(bars: list[dict[str, Any]]) -> float:
    """按首根 close 选缩放因子 k，使 close×k ≈ _TARGET_PRICE。空/异常回退 1.0。"""
    for b in bars:
        c = b.get("close")
        if c and c > 0:
            return _TARGET_PRICE / float(c)
    return 1.0


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
