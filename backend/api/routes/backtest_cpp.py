"""
C++ 回测引擎 API 代理路由

将前端请求转发到 C++ 回测服务 (localhost:8002)。
前端只和 FastAPI(:8000) 通信，不直接访问 C++ 服务。
"""
import asyncio
import httpx
import itertools
import json
import math
import queue
import threading
import uuid
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from loguru import logger

from data_engine.storage.history_repository import HistoryRepository

router = APIRouter(prefix="/backtest_cpp", tags=["C++回测引擎"])

# C++ 回测服务地址
CPP_SERVICE_URL = "http://localhost:8002"
TIMEOUT = 10.0
BATCH_TIMEOUT = 60.0  # 批量模式超时更长（C++ 服务可能排队）


# ── 请求模型 ──

class RiskConfigModel(BaseModel):
    enabled: bool = Field(default=False, description="是否启用风控")
    stop_loss_pct: float = Field(default=0.05, description="固定止损比例")
    trailing_stop: bool = Field(default=False, description="是否启用追踪止损")
    trailing_stop_pct: float = Field(default=0.08, description="追踪止损回撤比例")
    max_position_pct: float = Field(default=1.0, description="单股最大仓位占比")


class BacktestRunRequest(BaseModel):
    symbol: str = Field(..., description="股票代码")
    strategy: str = Field(default="MA_CROSS", description="策略名称: MA_CROSS / MOMENTUM")
    params: Dict[str, Any] = Field(default={}, description="策略参数")
    bars: Optional[List[Dict[str, Any]]] = Field(default=None, description="K线数据(可选)")
    initial_capital: float = Field(default=100000.0, description="初始资金")
    market: str = Field(default="a_share", description="市场: a_share / us / hk")
    start_date: str = Field(default="", description="开始日期")
    end_date: str = Field(default="", description="结束日期")
    slippage_pct: Optional[float] = Field(default=None, description="自定义滑点比例(None=用市场默认)")
    risk_config: Optional[RiskConfigModel] = Field(default=None, description="风控配置")


# ── 代理工具函数（同步 requests，在线程池中运行，不受事件循环阻塞影响） ──

def _proxy_sync(method: str, path: str, body: dict = None, timeout: float = None) -> dict:
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

        return resp.json()
    except requests.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="C++ 回测服务未启动。请先运行: cd backtest_cpp/build && ./backtest_server"
        )
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="C++ 回测服务响应超时")


async def _proxy(method: str, path: str, body: dict = None) -> dict:
    """转发请求到 C++ 服务（线程池隔离，不受事件循环阻塞影响）"""
    return await asyncio.to_thread(_proxy_sync, method, path, body)


def _safe_float(val, default=0.0):
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


# ── API 端点 ──

@router.get("/strategies")
async def get_strategies():
    """获取可用策略列表"""
    result = await _proxy("GET", "/api/strategies")
    return result


@router.post("/run")
async def run_backtest(req: BacktestRunRequest):
    """运行 C++ 回测"""
    body = req.model_dump(exclude_none=True)

    # 如果前端没有传 bars，从 DataEngine 获取数据（在线程池中运行，避免阻塞事件循环）
    if not body.get("bars"):
        try:
            def _fetch_bars():
                from data_engine import DataEngine
                de = DataEngine()
                return de.get_daily_data(
                    req.symbol,
                    start_date=req.start_date or None,
                    end_date=req.end_date or None
                )
            df = await asyncio.to_thread(_fetch_bars)
            if df is not None and not df.empty:
                bars = []
                for _, row in df.iterrows():
                    bars.append({
                        "date": str(row.get("date", row.name))[:10],
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row.get("volume", 0))
                    })
                body["bars"] = bars
                logger.info(f"从 DataEngine 获取 {len(bars)} 根K线: {req.symbol}")
        except Exception as e:
            logger.warning(f"从 DataEngine 获取数据失败: {e}, 将使用模拟数据")

    result = await _proxy("POST", "/api/backtest/run", body)
    return result


# ── 运行 + 持久化 ──

class BacktestRunAndSaveRequest(BaseModel):
    symbol: str = Field(..., description="股票代码")
    strategy: str = Field(default="MA_CROSS", description="策略名称")
    params: Dict[str, Any] = Field(default={}, description="策略参数")
    bars: Optional[List[Dict[str, Any]]] = Field(default=None, description="K线数据(可选)")
    initial_capital: float = Field(default=100000.0, description="初始资金")
    market: str = Field(default="a_share", description="市场: a_share / us / hk")
    start_date: str = Field(default="", description="开始日期")
    end_date: str = Field(default="", description="结束日期")
    name: Optional[str] = Field(default=None, description="任务名称")
    # PAIRS 策略用
    symbol2: Optional[str] = Field(default=None, description="配对股票代码(PAIRS策略)")
    # 滑点和风控
    slippage_pct: Optional[float] = Field(default=None, description="自定义滑点比例")
    risk_config: Optional[RiskConfigModel] = Field(default=None, description="风控配置")


@router.post("/run_and_save")
async def run_and_save(req: BacktestRunAndSaveRequest):
    """运行 C++ 回测并将结果持久化到数据库"""
    task_id = None
    repo = None
    try:
        # 1. 生成 task_id
        task_id = f"cpp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 2. 保存任务
        repo = HistoryRepository()  # noqa: assigned to outer scope for finally
        symbols = [req.symbol]
        if req.symbol2:
            symbols.append(req.symbol2)

        strategy_type = f"CPP_{req.strategy}"

        task = repo.save_backtest_task(
            task_id=task_id,
            name=req.name or f"C++ {req.strategy} - {req.symbol}",
            strategy_type=strategy_type,
            symbols=symbols,
            start_date=datetime.strptime(req.start_date, '%Y-%m-%d').date() if req.start_date else datetime.now().date(),
            end_date=datetime.strptime(req.end_date, '%Y-%m-%d').date() if req.end_date else datetime.now().date(),
            initial_capital=req.initial_capital,
            strategy_params=req.params
        )
        repo.update_backtest_status(task_id, "running")

        # 3. 准备回测请求体
        body = req.model_dump(exclude={"name", "symbol2"}, exclude_none=True)

        # 获取主股票的 K 线数据
        if not body.get("bars"):
            try:
                def _fetch_bars():
                    from data_engine import DataEngine
                    de = DataEngine()
                    return de.get_daily_data(
                        req.symbol,
                        start_date=req.start_date or None,
                        end_date=req.end_date or None
                    )
                df = await asyncio.to_thread(_fetch_bars)
                if df is not None and not df.empty:
                    bars = []
                    for _, row in df.iterrows():
                        bars.append({
                            "date": str(row.get("date", row.name))[:10],
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row.get("volume", 0))
                        })
                    body["bars"] = bars
                    logger.info(f"[C++ run_and_save] 获取 {len(bars)} 根K线: {req.symbol}")
            except Exception as e:
                logger.warning(f"获取K线数据失败: {e}")

        # PAIRS 策略：获取第二只股票数据
        if req.strategy == "PAIRS" and req.symbol2:
            try:
                def _fetch_bars2():
                    from data_engine import DataEngine
                    de = DataEngine()
                    return de.get_daily_data(
                        req.symbol2,
                        start_date=req.start_date or None,
                        end_date=req.end_date or None
                    )
                df2 = await asyncio.to_thread(_fetch_bars2)
                if df2 is not None and not df2.empty:
                    bars2 = []
                    for _, row in df2.iterrows():
                        bars2.append({
                            "date": str(row.get("date", row.name))[:10],
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row.get("volume", 0))
                        })
                    # 把 bars2 放到 params 中传给 C++ 服务
                    if "params" not in body:
                        body["params"] = {}
                    body["params"]["bars2"] = bars2
                    body["params"]["symbol2"] = req.symbol2
                    logger.info(f"[C++ run_and_save] 获取配对股票 {len(bars2)} 根K线: {req.symbol2}")
            except Exception as e:
                logger.warning(f"获取配对股票数据失败: {e}")

        # 4. 调用 C++ 回测服务
        cpp_result = await _proxy("POST", "/api/backtest/run", body)

        # 5. 映射指标到数据库格式（_safe_float 防止 None/NaN/Infinity/垃圾值）
        cpp_metrics = cpp_result.get("metrics", {})
        initial = req.initial_capital
        final_val = _safe_float(cpp_metrics.get("final_value"), initial)
        total_return = final_val - initial
        total_return_pct = _safe_float(cpp_metrics.get("total_return")) * 100  # C++ 返回小数
        annual_return = _safe_float(cpp_metrics.get("annualized_return")) * 100

        metrics = {
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "annual_return": annual_return,
            "final_value": final_val,
            "max_drawdown": _safe_float(cpp_metrics.get("max_drawdown_amount")),
            "max_drawdown_pct": _safe_float(cpp_metrics.get("max_drawdown")) * 100,
            "volatility": _safe_float(cpp_metrics.get("volatility")),
            "sharpe_ratio": _safe_float(cpp_metrics.get("sharpe_ratio")),
            "sortino_ratio": _safe_float(cpp_metrics.get("sortino_ratio")),
            "total_trades": int(_safe_float(cpp_metrics.get("total_trades"))),
            "winning_trades": int(_safe_float(cpp_metrics.get("winning_trades"))),
            "losing_trades": int(_safe_float(cpp_metrics.get("losing_trades"))),
            "win_rate": _safe_float(cpp_metrics.get("win_rate")) * 100,
            "profit_factor": _safe_float(cpp_metrics.get("profit_factor")),
            "total_commission": _safe_float(cpp_metrics.get("total_commission")),
            "total_slippage": _safe_float(cpp_metrics.get("total_slippage")),
        }

        # 转换资金曲线
        daily_records = []
        for eq in cpp_result.get("equity_curve", []):
            daily_records.append({
                "date": eq.get("date", ""),
                "total_value": eq.get("total_value", 0),
                "cash": eq.get("cash", 0),
                "market_value": eq.get("market_value", 0),
                "daily_return": eq.get("daily_return", 0),
            })

        # 转换交易记录
        trade_records = []
        for t in cpp_result.get("trades", []):
            trade_records.append({
                "date": t.get("date", ""),
                "symbol": t.get("symbol", req.symbol),
                "action": t.get("side", ""),
                "quantity": t.get("quantity", 0),
                "price": t.get("price", 0),
                "commission": t.get("commission", 0),
                "slippage": t.get("slippage", 0),
                "amount": t.get("price", 0) * t.get("quantity", 0),
                "reason": t.get("reason", "signal"),
            })

        # 6. 获取基准数据
        benchmark_data = None
        try:
            def _fetch_benchmark():
                from services.benchmark_service import BenchmarkService
                svc = BenchmarkService()
                return svc.get_benchmark_curve(
                    market=req.market,
                    start_date=req.start_date,
                    end_date=req.end_date,
                    initial_capital=req.initial_capital,
                )
            benchmark_data = await asyncio.to_thread(_fetch_benchmark)
            if benchmark_data:
                metrics["benchmark_return_pct"] = benchmark_data["benchmark_return_pct"]
                metrics["excess_return_pct"] = round(total_return_pct - benchmark_data["benchmark_return_pct"], 2)
                logger.info(f"基准 {benchmark_data['benchmark_name']}: {benchmark_data['benchmark_return_pct']}%")
        except Exception as e:
            logger.warning(f"获取基准数据失败: {e}")

        # 7. 保存结果
        repo.save_backtest_result(
            task_id=task_id,
            metrics=metrics,
            daily_records=daily_records,
            trade_records=trade_records
        )

        # 将 market 存入 strategy_params 方便历史查看
        try:
            from data_engine.storage.models import BacktestTask as _BT
            task_obj = repo.session.query(_BT).filter(_BT.task_id == task_id).first()
            if task_obj:
                import json as json_mod
                params = json_mod.loads(task_obj.strategy_params) if task_obj.strategy_params else {}
                params["market"] = req.market
                task_obj.strategy_params = json_mod.dumps(params, ensure_ascii=False)
                repo.session.commit()
        except Exception as e:
            logger.debug(f"保存 market 到 strategy_params 失败: {e}")

        repo.update_backtest_status(task_id, "completed")
        repo.close()

        # 8. 返回结果
        result = {
            "task_id": task_id,
            "strategy_name": cpp_result.get("strategy_name", req.strategy),
            "symbol": req.symbol,
            "metrics": metrics,
            "daily_records": daily_records,
            "trade_records": trade_records,
        }
        if benchmark_data:
            result["benchmark"] = benchmark_data
        return result

    except HTTPException:
        if repo:
            try:
                repo.close()
            except Exception:
                pass
        raise
    except Exception as e:
        # 标记任务失败
        try:
            if task_id:
                fail_repo = HistoryRepository()
                fail_repo.update_backtest_status(task_id, "failed", str(e))
                fail_repo.close()
        except Exception:
            pass
        if repo:
            try:
                repo.close()
            except Exception:
                pass
        logger.error(f"C++ 回测失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# 批量回测
# ══════════════════════════════════════════════════════════════

MAX_BATCH_TASKS = 500

class BatchBacktestRequest(BaseModel):
    mode: str = Field(..., description="multi_symbol / multi_strategy / param_optimize")
    # 通用
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    initial_capital: float = Field(default=100000.0)
    market: str = Field(default="a_share")
    name: Optional[str] = None
    # multi_symbol
    symbols: Optional[List[str]] = None
    strategy: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    # multi_strategy
    symbol: Optional[str] = None
    strategies: Optional[List[Dict[str, Any]]] = None  # [{strategy, params}]
    # param_optimize
    param_grid: Optional[Dict[str, List[Any]]] = None  # {param_name: [v1,v2,...]}


def _expand_batch_tasks(req: BatchBacktestRequest) -> List[Dict[str, Any]]:
    """将批量请求展开为子任务列表"""
    tasks: List[Dict[str, Any]] = []

    if req.mode == "multi_symbol":
        if not req.symbols or not req.strategy:
            raise HTTPException(status_code=400, detail="multi_symbol 模式需要 symbols 和 strategy")
        for sym in req.symbols:
            tasks.append({
                "symbol": sym,
                "strategy": req.strategy,
                "params": req.params or {},
                "label": sym,
            })

    elif req.mode == "multi_strategy":
        if not req.symbol or not req.strategies:
            raise HTTPException(status_code=400, detail="multi_strategy 模式需要 symbol 和 strategies")
        for s in req.strategies:
            tasks.append({
                "symbol": req.symbol,
                "strategy": s["strategy"],
                "params": s.get("params", {}),
                "label": s["strategy"],
            })

    elif req.mode == "param_optimize":
        if not req.symbol or not req.strategy or not req.param_grid:
            raise HTTPException(status_code=400, detail="param_optimize 模式需要 symbol, strategy 和 param_grid")
        keys = list(req.param_grid.keys())
        values = list(req.param_grid.values())
        for combo in itertools.product(*values):
            p = dict(zip(keys, combo))
            label = " ".join(f"{k}={v}" for k, v in p.items())
            base_params = dict(req.params or {})
            base_params.update(p)
            tasks.append({
                "symbol": req.symbol,
                "strategy": req.strategy,
                "params": base_params,
                "label": label,
            })

    else:
        raise HTTPException(status_code=400, detail=f"不支持的模式: {req.mode}")

    if len(tasks) > MAX_BATCH_TASKS:
        raise HTTPException(status_code=400, detail=f"子任务数 {len(tasks)} 超过上限 {MAX_BATCH_TASKS}")
    if not tasks:
        raise HTTPException(status_code=400, detail="展开后无子任务")

    return tasks


def _run_single_backtest_sync(
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

        body = {
            "symbol": symbol,
            "strategy": strategy,
            "params": params,
            "bars": bars,
            "initial_capital": initial_capital,
            "market": market,
            "start_date": start_date,
            "end_date": end_date,
        }

        # 调用 C++ 服务（同步，批量模式用更长超时）
        cpp_result = _proxy_sync("POST", "/api/backtest/run", body, timeout=BATCH_TIMEOUT)

        # 映射指标（_safe_float 防止 None/NaN/Infinity/垃圾值）
        cpp_metrics = cpp_result.get("metrics", {})
        final_val = _safe_float(cpp_metrics.get("final_value"), initial_capital)
        total_return = final_val - initial_capital
        total_return_pct = _safe_float(cpp_metrics.get("total_return")) * 100
        annual_return = _safe_float(cpp_metrics.get("annualized_return")) * 100

        metrics = {
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "annual_return": annual_return,
            "final_value": final_val,
            "max_drawdown": _safe_float(cpp_metrics.get("max_drawdown_amount")),
            "max_drawdown_pct": _safe_float(cpp_metrics.get("max_drawdown")) * 100,
            "volatility": _safe_float(cpp_metrics.get("volatility")),
            "sharpe_ratio": _safe_float(cpp_metrics.get("sharpe_ratio")),
            "sortino_ratio": _safe_float(cpp_metrics.get("sortino_ratio")),
            "total_trades": int(_safe_float(cpp_metrics.get("total_trades"))),
            "winning_trades": int(_safe_float(cpp_metrics.get("winning_trades"))),
            "losing_trades": int(_safe_float(cpp_metrics.get("losing_trades"))),
            "win_rate": _safe_float(cpp_metrics.get("win_rate")) * 100,
            "profit_factor": _safe_float(cpp_metrics.get("profit_factor")),
            "total_commission": _safe_float(cpp_metrics.get("total_commission")),
            "total_slippage": _safe_float(cpp_metrics.get("total_slippage")),
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
                sp["market"] = market
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


def _build_ranking(results: List[Dict], mode: str) -> List[Dict]:
    """按 sharpe_ratio 降序构建排行榜"""
    completed = [r for r in results if r.get("status") == "completed" and r.get("metrics")]
    completed.sort(key=lambda x: _safe_float(x["metrics"].get("sharpe_ratio"), -999), reverse=True)
    ranking = []
    for rank, r in enumerate(completed, 1):
        m = r["metrics"]
        ranking.append({
            "rank": rank,
            "task_id": r["task_id"],
            "symbol": r.get("symbol", ""),
            "strategy": r.get("strategy", ""),
            "params": r.get("params", {}),
            "label": r.get("label", ""),
            "total_return_pct": round(_safe_float(m.get("total_return_pct")), 2),
            "annual_return": round(_safe_float(m.get("annual_return")), 2),
            "sharpe_ratio": round(_safe_float(m.get("sharpe_ratio")), 4),
            "max_drawdown_pct": round(_safe_float(m.get("max_drawdown_pct")), 2),
            "win_rate": round(_safe_float(m.get("win_rate")), 2),
            "profit_factor": round(_safe_float(m.get("profit_factor")), 2),
            "total_trades": int(_safe_float(m.get("total_trades"))),
        })
    return ranking


@router.post("/batch")
async def batch_backtest(req: BatchBacktestRequest):
    """流式批量回测 (NDJSON)"""
    sub_tasks = _expand_batch_tasks(req)
    total = len(sub_tasks)
    batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    # 保存 BatchBacktest 记录
    from data_engine.storage.database import get_session
    from data_engine.storage.models import BatchBacktest
    db = get_session()
    try:
        batch = BatchBacktest(
            batch_id=batch_id,
            name=req.name or f"批量回测 {req.mode}",
            mode=req.mode,
            status="running",
            start_date=datetime.strptime(req.start_date, '%Y-%m-%d').date(),
            end_date=datetime.strptime(req.end_date, '%Y-%m-%d').date(),
            initial_capital=req.initial_capital,
            market=req.market,
            config=json.dumps(req.model_dump(), ensure_ascii=False),
            total_tasks=total,
            started_at=datetime.now(),
        )
        db.add(batch)
        db.commit()
    except Exception as e:
        logger.error(f"保存 BatchBacktest 失败: {e}")
    finally:
        db.close()

    def _ndjson(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False) + "\n"

    async def _streaming():
        results: List[Dict] = []
        completed_count = 0
        failed_count = 0

        # 发送 start 事件
        yield _ndjson({
            "event": "start",
            "batch_id": batch_id,
            "mode": req.mode,
            "total": total,
        })

        # 线程池 + queue 桥接
        result_queue: queue.Queue = queue.Queue()
        _SENTINEL = object()
        _cancelled = threading.Event()

        def _run_all():
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = {}
                for idx, task_spec in enumerate(sub_tasks):
                    if _cancelled.is_set():
                        break
                    future = executor.submit(
                        _run_single_backtest_sync,
                        symbol=task_spec["symbol"],
                        strategy=task_spec["strategy"],
                        params=task_spec["params"],
                        start_date=req.start_date,
                        end_date=req.end_date,
                        initial_capital=req.initial_capital,
                        market=req.market,
                        batch_id=batch_id,
                        task_label=task_spec["label"],
                    )
                    futures[future] = idx

                for future in as_completed(futures):
                    if _cancelled.is_set():
                        break
                    try:
                        res = future.result()
                    except Exception as e:
                        res = {"status": "failed", "error": str(e), "label": "unknown"}
                    result_queue.put(res)

            result_queue.put(_SENTINEL)

        thread = threading.Thread(target=_run_all, daemon=True)
        thread.start()

        try:
            while True:
                try:
                    item = result_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue

                if item is _SENTINEL:
                    break

                results.append(item)
                if item.get("status") == "completed":
                    completed_count += 1
                else:
                    failed_count += 1

                # 推送 progress 事件
                latest_info = {
                    "label": item.get("label", ""),
                    "symbol": item.get("symbol", ""),
                    "strategy": item.get("strategy", ""),
                    "status": item.get("status", ""),
                    "task_id": item.get("task_id", ""),
                }
                if item.get("metrics"):
                    m = item["metrics"]
                    latest_info["sharpe_ratio"] = round(m.get("sharpe_ratio", 0), 4)
                    latest_info["total_return_pct"] = round(m.get("total_return_pct", 0), 2)
                if item.get("error"):
                    latest_info["error"] = item["error"]

                yield _ndjson({
                    "event": "progress",
                    "batch_id": batch_id,
                    "current": completed_count + failed_count,
                    "total": total,
                    "completed": completed_count,
                    "failed": failed_count,
                    "latest": latest_info,
                })

        except asyncio.CancelledError:
            _cancelled.set()
            logger.info(f"[Batch] 客户端断开，取消剩余任务: {batch_id}")
            # 更新 DB 状态，避免永远卡在 running
            db_cancel = get_session()
            try:
                b = db_cancel.query(BatchBacktest).filter(BatchBacktest.batch_id == batch_id).first()
                if b:
                    b.status = "cancelled"
                    b.completed_tasks = completed_count
                    b.failed_tasks = failed_count
                    b.completed_at = datetime.now()
                    b.error_message = "用户取消"
                    child_ids = [r.get("task_id") for r in results if r.get("task_id")]
                    b.child_task_ids = json.dumps(child_ids)
                    db_cancel.commit()
            except Exception as e:
                logger.warning(f"取消时更新 BatchBacktest 失败: {e}")
            finally:
                db_cancel.close()
            raise

        # 构建排行榜
        ranking = _build_ranking(results, req.mode)
        child_ids = [r.get("task_id") for r in results if r.get("task_id")]

        # 更新 BatchBacktest 记录
        db2 = get_session()
        try:
            b = db2.query(BatchBacktest).filter(BatchBacktest.batch_id == batch_id).first()
            if b:
                b.status = "completed"
                b.completed_tasks = completed_count
                b.failed_tasks = failed_count
                b.child_task_ids = json.dumps(child_ids)
                b.completed_at = datetime.now()
                db2.commit()
        except Exception as e:
            logger.warning(f"更新 BatchBacktest 失败: {e}")
        finally:
            db2.close()

        yield _ndjson({
            "event": "complete",
            "batch_id": batch_id,
            "total": total,
            "completed": completed_count,
            "failed": failed_count,
            "ranking": ranking,
        })

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/batch/{batch_id}")
async def get_batch_result(batch_id: str):
    """查询批量回测结果"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import BatchBacktest, BacktestTask as _BT, BacktestResult as _BR

    db = get_session()
    try:
        batch = db.query(BatchBacktest).filter(BatchBacktest.batch_id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="批量回测不存在")

        child_ids = json.loads(batch.child_task_ids) if batch.child_task_ids else []
        results = []
        for tid in child_ids:
            task = db.query(_BT).filter(_BT.task_id == tid).first()
            br = db.query(_BR).filter(_BR.task_id == tid).first()
            if task:
                item: Dict[str, Any] = {
                    "task_id": tid,
                    "symbol": json.loads(task.symbols)[0] if task.symbols else "",
                    "strategy": task.strategy_type.replace("CPP_", ""),
                    "params": json.loads(task.strategy_params) if task.strategy_params else {},
                    "status": task.status,
                    "label": task.name or "",
                }
                if br:
                    item["metrics"] = {
                        "total_return_pct": br.total_return_pct,
                        "annual_return": br.annual_return,
                        "sharpe_ratio": br.sharpe_ratio,
                        "max_drawdown_pct": br.max_drawdown_pct,
                        "win_rate": br.win_rate,
                        "profit_factor": br.profit_factor,
                        "total_trades": br.total_trades,
                    }
                results.append(item)

        ranking = _build_ranking(results, batch.mode)

        return {
            "batch_id": batch.batch_id,
            "name": batch.name,
            "mode": batch.mode,
            "status": batch.status,
            "total_tasks": batch.total_tasks,
            "completed_tasks": batch.completed_tasks,
            "failed_tasks": batch.failed_tasks,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
            "ranking": ranking,
            "results": results,
        }
    finally:
        db.close()


@router.get("/batches")
async def list_batches(limit: int = Query(default=20, le=100)):
    """批量回测历史列表"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import BatchBacktest

    db = get_session()
    try:
        batches = (
            db.query(BatchBacktest)
            .order_by(BatchBacktest.created_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "batches": [
                {
                    "batch_id": b.batch_id,
                    "name": b.name,
                    "mode": b.mode,
                    "status": b.status,
                    "total_tasks": b.total_tasks,
                    "completed_tasks": b.completed_tasks,
                    "failed_tasks": b.failed_tasks,
                    "market": b.market,
                    "created_at": b.created_at.isoformat() if b.created_at else None,
                    "completed_at": b.completed_at.isoformat() if b.completed_at else None,
                }
                for b in batches
            ]
        }
    finally:
        db.close()
