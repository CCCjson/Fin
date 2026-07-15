"""alpha_lab 信号回测核心 —— AI 逐 bar 产信号 → C++ run_signals 执行+算指标。

从 sandbox 的 subprocess runner 抽出来，便于单测（不用起子进程也能验）。

设计要点（域5 阶段②）：
- **防未来函数（结构性）**：`collect_signals` 逐 bar 只把 `df.iloc[:i+1]`
  （从第一根到当前 bar 的历史，**无未来行**）喂给 AI 的 `on_bar`，AI 代码
  拿不到未来数据。这是整套改造的安全命门。
- **执行/费用/指标口径统一**：撮合/次日开盘/T+1/滑点/费用/全套指标一律
  由 C++ `/api/backtest/run_signals` 产出（复用 `backtest_cpp_client.run_signals`），
  Python 侧只做「产信号 + 量纲归一化」。
- **止损下沉引擎**：AI 的 on_bar 是 stateless（拿不到入场价），无法按入场价
  止损；固定止损由引擎 `risk_config` 强制执行。
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

import pandas as pd

from services.backtest_cpp_client import run_signals, safe_float

# AI on_bar 的返回：'buy' / 'sell' / None
OnBar = Callable[[pd.DataFrame], str | None]


def _date_str(idx: Any) -> str:
    """把索引值（DatetimeIndex 或字符串）归一成 YYYY-MM-DD。"""
    if hasattr(idx, "strftime"):
        return idx.strftime("%Y-%m-%d")
    return str(idx)[:10]


def collect_signals(on_bar: OnBar, df: pd.DataFrame) -> list[dict]:
    """逐 bar 驱动 AI 的 on_bar，收成干净的进出场信号事件。

    - **只喂历史**：第 i 根传 `df.iloc[:i+1]`（含当前 bar，无未来），杜绝未来函数。
    - **边沿去重**（状态机 flat↔long）：flat 见 buy→发 buy 转 long；long 见 sell→
      发 sell 转 flat；其余（持仓中重复 buy、空仓 sell）忽略——避免金字塔加仓/空转，
      与内置策略「单持仓」语义对齐。

    Returns:
        [{"date": "YYYY-MM-DD", "action": "buy"|"sell"}, ...]
    """
    dates = [_date_str(idx) for idx in df.index]
    signals: list[dict] = []
    state = "flat"
    for i in range(len(df)):
        history = df.iloc[: i + 1]
        raw = on_bar(history)   # 单 bar 异常不吞：交给 backtest_via_cpp 顶层捕获=策略失败
        act = raw.lower() if isinstance(raw, str) else None
        if act == "buy" and state == "flat":
            signals.append({"date": dates[i], "action": "buy"})
            state = "long"
        elif act == "sell" and state == "long":
            signals.append({"date": dates[i], "action": "sell"})
            state = "flat"
    return signals


def normalize_metrics(cpp_metrics: dict) -> dict:
    """C++ 原始 metrics（小数口径）→ Evaluator/iterate prompt 期望的 schema。

    换算：total_return/annualized_return/win_rate ×100；max_drawdown 折成嵌套
    dict；total_trades→num_trades。safe_float 防 None/NaN/Inf。
    """
    return {
        "sharpe_ratio": safe_float(cpp_metrics.get("sharpe_ratio")),
        "sortino_ratio": safe_float(cpp_metrics.get("sortino_ratio")),
        "total_return": safe_float(cpp_metrics.get("total_return")) * 100,
        "annualized_return": safe_float(cpp_metrics.get("annualized_return")) * 100,
        "win_rate": safe_float(cpp_metrics.get("win_rate")) * 100,
        "profit_factor": safe_float(cpp_metrics.get("profit_factor")),
        "num_trades": int(safe_float(cpp_metrics.get("total_trades"))),
        "final_value": safe_float(cpp_metrics.get("final_value")),
        "max_drawdown": {
            "max_drawdown_pct": safe_float(cpp_metrics.get("max_drawdown")) * 100,
            "max_drawdown_amount": safe_float(cpp_metrics.get("max_drawdown_amount")),
        },
        "total_commission": safe_float(cpp_metrics.get("total_commission")),
        "total_slippage": safe_float(cpp_metrics.get("total_slippage")),
    }


def _read_csv(path: str) -> pd.DataFrame:
    """读 _prepare_data 落的 CSV：date 是索引（index_col=0），含 OHLCV + 指标列。"""
    return pd.read_csv(path, index_col=0, parse_dates=True)


def _to_bars(df: pd.DataFrame) -> list[dict]:
    """DataFrame → C++ 需要的 bars 序列（date 取索引）。"""
    bars: list[dict] = []
    for idx, row in df.iterrows():
        bars.append({
            "date": _date_str(idx),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0) or 0),
        })
    return bars


def backtest_via_cpp(
    strategy_module: Any,
    train_csv: str,
    val_csv: str,
    symbol: str,
    capital: float = 1_000_000.0,
    market: str = "a_share",
    stop_loss_pct: float = 0.08,
) -> dict:
    """对 train/val 各跑一遍「产信号→C++ 执行→归一化指标」。

    Args:
        strategy_module: 已加载的 AI 策略模块，需暴露 `on_bar(history)`。
        train_csv/val_csv: `_prepare_data` 落的 CSV 路径。
        symbol/capital/market: 回测参数。
        stop_loss_pct: 引擎强制固定止损比例（>0 时启用 risk_config）。

    Returns:
        {"success": bool, "error": str|None, "train_metrics": {...}, "val_metrics": {...}}
        —— 与旧 sandbox 契约同形状，evaluator/迭代循环无感。
    """
    on_bar = getattr(strategy_module, "on_bar", None)
    if not callable(on_bar):
        return {
            "success": False,
            "error": "策略代码缺少 on_bar(history) 函数",
            "train_metrics": {},
            "val_metrics": {},
        }

    risk_config = (
        {"enabled": True, "stop_loss_pct": stop_loss_pct}
        if stop_loss_pct and stop_loss_pct > 0
        else None
    )

    try:
        out: dict = {}
        for key, csv_path in (("train_metrics", train_csv), ("val_metrics", val_csv)):
            df = _read_csv(csv_path)
            bars = _to_bars(df)
            signals = collect_signals(on_bar, df)
            cpp = run_signals(
                bars=bars,
                signals=signals,
                initial_capital=capital,
                market=market,
                symbol=symbol,
                risk_config=risk_config,
            )
            out[key] = normalize_metrics(cpp.get("metrics", {}))
        return {"success": True, "error": None, **out}
    except Exception:
        return {
            "success": False,
            "error": traceback.format_exc()[-1500:],
            "train_metrics": {},
            "val_metrics": {},
        }
