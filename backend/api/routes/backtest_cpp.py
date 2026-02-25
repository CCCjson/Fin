"""
C++ 回测引擎 API 代理路由

将前端请求转发到 C++ 回测服务 (localhost:8002)。
前端只和 FastAPI(:8000) 通信，不直接访问 C++ 服务。
"""
import asyncio
import httpx
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from loguru import logger

router = APIRouter(prefix="/backtest_cpp", tags=["C++回测引擎"])

# C++ 回测服务地址
CPP_SERVICE_URL = "http://localhost:8002"
TIMEOUT = 10.0


# ── 请求模型 ──

class BacktestRunRequest(BaseModel):
    symbol: str = Field(..., description="股票代码")
    strategy: str = Field(default="MA_CROSS", description="策略名称: MA_CROSS / MOMENTUM")
    params: Dict[str, Any] = Field(default={}, description="策略参数")
    bars: Optional[List[Dict[str, Any]]] = Field(default=None, description="K线数据(可选)")
    initial_capital: float = Field(default=100000.0, description="初始资金")
    market: str = Field(default="a_share", description="市场: a_share / us / hk")
    start_date: str = Field(default="", description="开始日期")
    end_date: str = Field(default="", description="结束日期")


# ── 代理工具函数（同步 requests，在线程池中运行，不受事件循环阻塞影响） ──

def _proxy_sync(method: str, path: str, body: dict = None) -> dict:
    """转发请求到 C++ 服务（同步版本，运行在线程池）"""
    url = f"{CPP_SERVICE_URL}{path}"
    try:
        if method == "GET":
            resp = requests.get(url, timeout=TIMEOUT)
        elif method == "POST":
            resp = requests.post(url, json=body or {}, timeout=TIMEOUT)
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


# ── API 端点 ──

@router.get("/strategies")
async def get_strategies():
    """获取可用策略列表"""
    result = await _proxy("GET", "/api/strategies")
    return result


@router.post("/run")
async def run_backtest(req: BacktestRunRequest):
    """运行 C++ 回测"""
    body = req.model_dump()

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
