"""
Walk-Forward 验证 API

将历史数据分成多个滚动窗口，在训练期优化参数，在测试期验证表现。
多个窗口的测试期拼接起来，就是样本外的真实表现。
"""
import asyncio
import itertools
import json
import math
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/walk_forward", tags=["Walk-Forward验证"])


# ── Request Model ──

class WalkForwardRequest(BaseModel):
    symbol: str = Field(..., description="股票代码")
    strategy: str = Field(default="MA_CROSS", description="策略名称")
    market: str = Field(default="a_share", description="市场")
    initial_capital: float = Field(default=100000.0)
    train_months: int = Field(default=12, description="训练期长度(月)")
    test_months: int = Field(default=3, description="测试期长度(月)")
    step_months: int = Field(default=3, description="滚动步长(月)")
    start_date: str = Field(..., description="整体起始日期")
    end_date: str = Field(..., description="整体结束日期")
    param_grid: Dict[str, List[Any]] = Field(..., description="参数搜索范围")
    slippage_pct: Optional[float] = Field(default=None, description="自定义滑点")
    risk_config: Optional[Dict[str, Any]] = Field(default=None, description="风控配置")


# ── Helper Functions ──

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


def _generate_windows(start_date: str, end_date: str,
                      train_months: int, test_months: int,
                      step_months: int) -> List[Dict[str, str]]:
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
    symbol: str, strategy: str, params: Dict,
    start_date: str, end_date: str,
    initial_capital: float, market: str,
    slippage_pct: Optional[float] = None,
    risk_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """调用 C++ 回测服务，返回结果（同步，在线程池中运行）"""
    import requests

    body: Dict[str, Any] = {
        "symbol": symbol,
        "strategy": strategy,
        "params": params,
        "initial_capital": initial_capital,
        "market": market,
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

    cpp_url = "http://localhost:8002/api/backtest/run"
    resp = requests.post(cpp_url, json=body, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"C++ backtest failed: {resp.text[:200]}")

    return resp.json()


def _optimize_on_period(
    symbol: str, strategy: str,
    param_grid: Dict[str, List[Any]],
    start_date: str, end_date: str,
    initial_capital: float, market: str,
    slippage_pct: Optional[float],
    risk_config: Optional[Dict],
) -> Dict[str, Any]:
    """在训练期上网格搜索最优参数（按 Sharpe 排序）"""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))

    best_sharpe = -999.0
    best_params = dict(zip(keys, combos[0])) if combos else {}
    best_metrics = {}

    for combo in combos:
        params = dict(zip(keys, combo))
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


# ── API Endpoint ──

@router.post("/run")
async def run_walk_forward(req: WalkForwardRequest):
    """流式 Walk-Forward 验证 (NDJSON)"""

    windows = _generate_windows(
        req.start_date, req.end_date,
        req.train_months, req.test_months, req.step_months,
    )

    if not windows:
        raise HTTPException(status_code=400, detail="无法生成有效的时间窗口，请检查日期范围和训练/测试期长度")

    total = len(windows)
    wf_id = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def _ndjson(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False) + "\n"

    async def _streaming():
        yield _ndjson({
            "event": "start",
            "wf_id": wf_id,
            "total_windows": total,
            "windows": windows,
        })

        result_queue: queue.Queue = queue.Queue()
        _SENTINEL = object()

        def _run_all():
            for idx, window in enumerate(windows):
                try:
                    # 1. 训练期：参数优化
                    opt = _optimize_on_period(
                        req.symbol, req.strategy, req.param_grid,
                        window["train_start"], window["train_end"],
                        req.initial_capital, req.market,
                        req.slippage_pct, req.risk_config,
                    )

                    # 2. 测试期：用最优参数回测
                    test_result = _run_single_cpp_backtest(
                        req.symbol, req.strategy, opt["best_params"],
                        window["test_start"], window["test_end"],
                        req.initial_capital, req.market,
                        req.slippage_pct, req.risk_config,
                    )

                    test_metrics = test_result.get("metrics", {})
                    test_equity = test_result.get("equity_curve", [])

                    result_queue.put({
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
                    })
                except Exception as e:
                    result_queue.put({
                        "idx": idx,
                        "window": window,
                        "status": "failed",
                        "error": str(e),
                    })

            result_queue.put(_SENTINEL)

        thread = threading.Thread(target=_run_all, daemon=True)
        thread.start()

        all_results = []
        completed = 0

        while True:
            try:
                item = result_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.2)
                continue

            if item is _SENTINEL:
                break

            all_results.append(item)
            completed += 1

            yield _ndjson({
                "event": "window_done",
                "wf_id": wf_id,
                "current": completed,
                "total": total,
                **item,
            })

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

        yield _ndjson({
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
        })

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
