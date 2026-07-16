"""
Walk-Forward 验证引擎 —— 滚动窗口训练/测试的整套算法。

从 api/routes/walk_forward.py 下沉而来（域9 厚 route 下沉）：route 只做 NDJSON
编码 + 流式桥接，滚动窗口生成 / 训练期网格寻优 / 测试期回放 / 样本外汇总统计
全在这里。C++ 回测统一走 services.backtest_cpp_client.proxy_sync（复用共享传输，
market 经 to_cpp_market 转 wire 值——修掉旧 route 里裸传 canonical 导致港美股
静默拿错市场的 bug），且**不落库**（网格搜索每个 combo 都跑，落库会灌爆历史表）。

用法：
    windows = generate_windows(start, end, train_m, test_m, step_m)
    if not windows: ...            # route 侧校验空窗抛 400
    for event in run_walk_forward(symbol=..., windows=windows, ...):
        ...                        # event: start / window_done / complete
"""
import itertools
import math
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta
from loguru import logger

from common.market import to_cpp_market
from services.backtest_cpp_client import proxy_sync

_CPP_TIMEOUT = 30.0


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f) or abs(f) > 1e12:
            return default
        return f
    except (TypeError, ValueError):
        return default


def generate_windows(start_date: str, end_date: str,
                     train_months: int, test_months: int,
                     step_months: int) -> list[dict[str, str]]:
    """生成滚动时间窗口列表"""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    windows = []
    current = start

    while True:
        train_start = current
        train_end = current + relativedelta(months=train_months) - timedelta(days=1)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + relativedelta(months=test_months) - timedelta(days=1)

        if test_end > end:
            break

        windows.append({
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
        })

        current += relativedelta(months=step_months)

    return windows


def _run_single_cpp_backtest(
    symbol: str, strategy: str, params: dict,
    start_date: str, end_date: str,
    initial_capital: float, market: str,
    slippage_pct: float | None = None,
    risk_config: dict | None = None,
) -> dict[str, Any]:
    """调用 C++ 回测服务，返回结果（同步；不落库）。market 经 to_cpp_market 转 wire 值。"""
    body: dict[str, Any] = {
        "symbol": symbol,
        "strategy": strategy,
        "params": params,
        "initial_capital": initial_capital,
        "market": to_cpp_market(market),  # canonical → C++ wire(us/hk/a_share)
        "start_date": start_date,
        "end_date": end_date,
    }
    if slippage_pct is not None and slippage_pct >= 0:
        body["slippage_pct"] = slippage_pct
    if risk_config:
        body["risk_config"] = risk_config

    # 获取 K 线数据
    try:
        from data_engine import DataEngine
        de = DataEngine()
        df = de.get_daily_data(symbol, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            bars = []
            for _, row in df.iterrows():
                bars.append({
                    "date": str(row.get("date", row.name))[:10],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0)),
                })
            body["bars"] = bars
    except Exception as e:
        logger.warning(f"[WalkForward] 获取K线失败 {symbol}: {e}")

    return proxy_sync("POST", "/api/backtest/run", body, timeout=_CPP_TIMEOUT)


def _optimize_on_period(
    symbol: str, strategy: str,
    param_grid: dict[str, list[Any]],
    start_date: str, end_date: str,
    initial_capital: float, market: str,
    slippage_pct: float | None,
    risk_config: dict | None,
) -> dict[str, Any]:
    """在训练期上网格搜索最优参数（按 Sharpe 排序）"""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))

    best_sharpe = -999.0
    best_params = dict(zip(keys, combos[0], strict=False)) if combos else {}
    best_metrics = {}

    for combo in combos:
        params = dict(zip(keys, combo, strict=False))
        try:
            result = _run_single_cpp_backtest(
                symbol, strategy, params,
                start_date, end_date,
                initial_capital, market,
                slippage_pct, risk_config,
            )
            metrics = result.get("metrics", {})
            sharpe = _safe_float(metrics.get("sharpe_ratio"), -999)
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_params = params
                best_metrics = metrics
        except Exception as e:
            logger.debug(f"[WalkForward] 训练期回测失败 {params}: {e}")
            continue

    return {
        "best_params": best_params,
        "train_sharpe": round(best_sharpe, 4) if best_sharpe > -900 else 0.0,
        "train_metrics": best_metrics,
        "combos_tested": len(combos),
    }


def run_walk_forward(
    *,
    symbol: str,
    strategy: str,
    market: str,
    initial_capital: float,
    param_grid: dict[str, list[Any]],
    windows: list[dict[str, str]],
    slippage_pct: float | None = None,
    risk_config: dict | None = None,
) -> Iterator[dict[str, Any]]:
    """逐窗「训练期寻优 → 测试期回放」并逐个产出事件（同步生成器，供流式桥接线程驱动）。

    产出事件（与旧 route 逐字段一致）：
      start        → wf_id / total_windows / windows
      window_done  → wf_id / current / total + 单窗结果（idx/window/best_params/test_* 等）
      complete     → wf_id / summary / param_history / oos_equity / windows(全量结果)
    """
    total = len(windows)
    wf_id = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    yield {
        "event": "start",
        "wf_id": wf_id,
        "total_windows": total,
        "windows": windows,
    }

    all_results: list[dict[str, Any]] = []
    completed = 0

    for idx, window in enumerate(windows):
        try:
            # 1. 训练期：参数优化
            opt = _optimize_on_period(
                symbol, strategy, param_grid,
                window["train_start"], window["train_end"],
                initial_capital, market,
                slippage_pct, risk_config,
            )

            # 2. 测试期：用最优参数回测
            test_result = _run_single_cpp_backtest(
                symbol, strategy, opt["best_params"],
                window["test_start"], window["test_end"],
                initial_capital, market,
                slippage_pct, risk_config,
            )

            test_metrics = test_result.get("metrics", {})
            test_equity = test_result.get("equity_curve", [])

            item = {
                "idx": idx,
                "window": window,
                "best_params": opt["best_params"],
                "combos_tested": opt["combos_tested"],
                "train_sharpe": opt["train_sharpe"],
                "test_sharpe": round(_safe_float(test_metrics.get("sharpe_ratio")), 4),
                "test_return": round(_safe_float(test_metrics.get("total_return")) * 100, 2),
                "test_max_dd": round(_safe_float(test_metrics.get("max_drawdown")) * 100, 2),
                "test_win_rate": round(_safe_float(test_metrics.get("win_rate")) * 100, 2),
                "test_trades": int(_safe_float(test_metrics.get("total_trades"))),
                "test_equity": test_equity,
                "status": "completed",
            }
        except Exception as e:
            item = {
                "idx": idx,
                "window": window,
                "status": "failed",
                "error": str(e),
            }

        all_results.append(item)
        completed += 1

        yield {
            "event": "window_done",
            "wf_id": wf_id,
            "current": completed,
            "total": total,
            **item,
        }

    # 计算汇总
    completed_results = [r for r in all_results if r.get("status") == "completed"]

    # 样本外拼接
    oos_equity = []
    for r in sorted(completed_results, key=lambda x: x["idx"]):
        for eq in r.get("test_equity", []):
            oos_equity.append({
                "date": eq.get("date", ""),
                "total_value": eq.get("total_value", 0),
            })

    # 统计
    train_sharpes = [r["train_sharpe"] for r in completed_results]
    test_sharpes = [r["test_sharpe"] for r in completed_results]
    test_returns = [r["test_return"] for r in completed_results]

    avg_train_sharpe = sum(train_sharpes) / len(train_sharpes) if train_sharpes else 0
    avg_test_sharpe = sum(test_sharpes) / len(test_sharpes) if test_sharpes else 0
    avg_test_return = sum(test_returns) / len(test_returns) if test_returns else 0
    overfit_ratio = abs(avg_train_sharpe / avg_test_sharpe) if avg_test_sharpe != 0 else 999

    # 参数稳定性：看最优参数的变化
    param_history = [r.get("best_params", {}) for r in completed_results]

    yield {
        "event": "complete",
        "wf_id": wf_id,
        "total_windows": total,
        "completed_windows": len(completed_results),
        "failed_windows": total - len(completed_results),
        "summary": {
            "avg_train_sharpe": round(avg_train_sharpe, 4),
            "avg_test_sharpe": round(avg_test_sharpe, 4),
            "avg_test_return": round(avg_test_return, 2),
            "overfit_ratio": round(overfit_ratio, 2),
        },
        "param_history": param_history,
        "oos_equity": oos_equity,
        "windows": all_results,
    }
