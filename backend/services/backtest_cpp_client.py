"""
C++ 回测服务客户端 — 代理调用 + 单次回测（取数→调服务→落库）

从 api/routes/backtest_cpp.py 下沉而来：route 和 MoneyBill 工具（backtest_tools）
共用这一份，route 只做 HTTP 封装。

注意：proxy_sync 沿用 fastapi.HTTPException 表达 C++ 服务不可用/超时，
route 侧直接透传；run_single_backtest 内部捕获所有异常并返回 status=failed。
"""
import asyncio
import json
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List

import requests
from fastapi import HTTPException
from loguru import logger

from data_engine.storage.history_repository import HistoryRepository

# C++ 回测服务地址
CPP_SERVICE_URL = "http://localhost:8002"
TIMEOUT = 10.0
BATCH_TIMEOUT = 60.0  # 批量模式超时更长（C++ 服务可能排队）


def proxy_sync(method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
    """转发请求到 C++ 服务（同步版本，运行在线程池）"""
    url = f"{CPP_SERVICE_URL}{path}"
    _timeout = timeout or TIMEOUT
    try:
        if method == "GET":
            resp = requests.get(url, timeout=_timeout)
        elif method == "POST":
            resp = requests.post(url, json=body or {}, timeout=_timeout)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text or f"C++ 服务返回 {resp.status_code}"
            raise HTTPException(status_code=resp.status_code, detail=detail)

        data: dict = resp.json()
        return data
    except requests.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="C++ 回测服务未启动。请先运行: cd backtest_cpp/build && ./backtest_server"
        )
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="C++ 回测服务响应超时")


async def proxy(method: str, path: str, body: dict | None = None) -> dict:
    """转发请求到 C++ 服务（线程池隔离，不受事件循环阻塞影响）"""
    return await asyncio.to_thread(proxy_sync, method, path, body)


def run_signals(
    bars: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    initial_capital: float = 100000.0,
    market: str = "a_share",
    symbol: str = "SIGNAL",
    start_date: str = "",
    end_date: str = "",
    slippage_pct: float | None = None,
    risk_config: dict | None = None,
    timeout: float | None = None,
) -> Dict[str, Any]:
    """信号驱动回测：把外部信号序列发给 C++ /api/backtest/run_signals 拿全套指标。

    与 run_single_backtest 不同：不落库、不取数，bars/signals 由调用方直接提供
    （给 alpha_lab 沙箱等「AI 只产信号」的场景用）。返回 C++ 原始结果
    （metrics 为小数口径，量纲换算由调用方按需处理）。

    Args:
        bars: K 线序列 [{date, open, high, low, close, volume}, ...]
        signals: 信号序列 [{date, action, price?, weight?}, ...]，action=buy/sell
        initial_capital: 初始资金
        market: canonical 市场（a_share/hk_stock/us_stock），内部转 C++ wire 格式
        symbol: 标的代码（仅用于结果标注）
        start_date/end_date: 可选日期过滤
        slippage_pct: 可选滑点覆盖（>=0 生效）
        risk_config: 可选风控（引擎侧强制止损），如
            {"enabled": True, "stop_loss_pct": 0.08}

    Returns:
        C++ 服务返回的原始 dict（metrics/equity_curve/trades/dropped_last_bar_orders）
    """
    from common.market import to_cpp_market

    body: Dict[str, Any] = {
        "symbol": symbol,
        "signals": signals,
        "bars": bars,
        "initial_capital": initial_capital,
        "market": to_cpp_market(market),
        "start_date": start_date,
        "end_date": end_date,
    }
    if slippage_pct is not None:
        body["slippage_pct"] = slippage_pct
    if risk_config is not None:
        body["risk_config"] = risk_config

    return proxy_sync("POST", "/api/backtest/run_signals", body, timeout=timeout or BATCH_TIMEOUT)


def safe_float(val: Any, default: float = 0.0) -> float:
    """将 None / NaN / Infinity / 垃圾浮点数转为安全值"""
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f) or abs(f) > 1e12:
            return default
        return f
    except (TypeError, ValueError):
        return default


def run_single_backtest(
    symbol: str,
    strategy: str,
    params: Dict[str, Any],
    start_date: str,
    end_date: str,
    initial_capital: float,
    market: str,
    batch_id: str,
    task_label: str,
) -> Dict[str, Any]:
    """线程安全的单次回测（复用 run_and_save 核心逻辑）"""
    task_id = f"cpp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    repo = HistoryRepository()
    try:
        strategy_type = f"CPP_{strategy}"
        repo.save_backtest_task(
            task_id=task_id,
            name=f"[Batch] {strategy} {symbol} {task_label}",
            strategy_type=strategy_type,
            symbols=[symbol],
            start_date=datetime.strptime(start_date, '%Y-%m-%d').date(),
            end_date=datetime.strptime(end_date, '%Y-%m-%d').date(),
            initial_capital=initial_capital,
            strategy_params=params,
        )
        # 标记 batch_id
        from data_engine.storage.models import BacktestTask as _BT
        task_obj = repo.session.query(_BT).filter(_BT.task_id == task_id).first()
        if task_obj:
            task_obj.batch_id = batch_id
            repo.session.commit()

        repo.update_backtest_status(task_id, "running")

        # 获取 K 线数据
        from data_engine import DataEngine
        de = DataEngine()
        df = de.get_daily_data(symbol, start_date=start_date, end_date=end_date)
        bars = []
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                bars.append({
                    "date": str(row.get("date", row.name))[:10],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0)),
                })

        from common.market import to_cpp_market, normalize_market

        body = {
            "symbol": symbol,
            "strategy": strategy,
            "params": params,
            "bars": bars,
            "initial_capital": initial_capital,
            "market": to_cpp_market(market),  # canonical → C++ wire(us/hk/a_share)
            "start_date": start_date,
            "end_date": end_date,
        }

        # 调用 C++ 服务（同步，批量模式用更长超时）
        cpp_result = proxy_sync("POST", "/api/backtest/run", body, timeout=BATCH_TIMEOUT)

        # 映射指标（safe_float 防止 None/NaN/Infinity/垃圾值）
        cpp_metrics = cpp_result.get("metrics", {})
        final_val = safe_float(cpp_metrics.get("final_value"), initial_capital)
        total_return = final_val - initial_capital
        total_return_pct = safe_float(cpp_metrics.get("total_return")) * 100
        annual_return = safe_float(cpp_metrics.get("annualized_return")) * 100

        metrics = {
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "annual_return": annual_return,
            "final_value": final_val,
            "max_drawdown": safe_float(cpp_metrics.get("max_drawdown_amount")),
            "max_drawdown_pct": safe_float(cpp_metrics.get("max_drawdown")) * 100,
            "volatility": safe_float(cpp_metrics.get("volatility")),
            "sharpe_ratio": safe_float(cpp_metrics.get("sharpe_ratio")),
            "sortino_ratio": safe_float(cpp_metrics.get("sortino_ratio")),
            "total_trades": int(safe_float(cpp_metrics.get("total_trades"))),
            "winning_trades": int(safe_float(cpp_metrics.get("winning_trades"))),
            "losing_trades": int(safe_float(cpp_metrics.get("losing_trades"))),
            "win_rate": safe_float(cpp_metrics.get("win_rate")) * 100,
            "profit_factor": safe_float(cpp_metrics.get("profit_factor")),
            "total_commission": safe_float(cpp_metrics.get("total_commission")),
            "total_slippage": safe_float(cpp_metrics.get("total_slippage")),
        }

        daily_records = [
            {
                "date": eq.get("date", ""),
                "total_value": eq.get("total_value", 0),
                "cash": eq.get("cash", 0),
                "market_value": eq.get("market_value", 0),
                "daily_return": eq.get("daily_return", 0),
            }
            for eq in cpp_result.get("equity_curve", [])
        ]
        trade_records = [
            {
                "date": t.get("date", ""),
                "symbol": t.get("symbol", symbol),
                "action": t.get("side", ""),
                "quantity": t.get("quantity", 0),
                "price": t.get("price", 0),
                "commission": t.get("commission", 0),
                "slippage": t.get("slippage", 0),
                "amount": t.get("price", 0) * t.get("quantity", 0),
                "reason": t.get("reason", "signal"),
            }
            for t in cpp_result.get("trades", [])
        ]

        # 保存 market 到 strategy_params
        try:
            task_obj = repo.session.query(_BT).filter(_BT.task_id == task_id).first()
            if task_obj:
                sp = json.loads(task_obj.strategy_params) if task_obj.strategy_params else {}
                sp["market"] = normalize_market(market)  # 落库统一存 canonical
                task_obj.strategy_params = json.dumps(sp, ensure_ascii=False)
                repo.session.commit()
        except Exception:
            pass

        repo.save_backtest_result(
            task_id=task_id,
            metrics=metrics,
            daily_records=daily_records,
            trade_records=trade_records,
        )
        repo.update_backtest_status(task_id, "completed")
        repo.close()

        return {
            "task_id": task_id,
            "symbol": symbol,
            "strategy": strategy,
            "params": params,
            "label": task_label,
            "metrics": metrics,
            "status": "completed",
        }

    except Exception as e:
        try:
            repo.update_backtest_status(task_id, "failed", str(e))
            repo.close()
        except Exception:
            pass
        logger.warning(f"[Batch] 子任务失败 {symbol}/{strategy}: {e}")
        return {
            "task_id": task_id,
            "symbol": symbol,
            "strategy": strategy,
            "params": params,
            "label": task_label,
            "metrics": None,
            "status": "failed",
            "error": str(e),
        }
