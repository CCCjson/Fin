"""
回测相关API
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import pandas as pd

from api.models.schemas import BacktestRequest, BacktestResponse
from data_engine import DataEngine
from backtest_engine import BacktestEngine
from backtest_engine.strategies import MACrossStrategy, SignalStrategy

router = APIRouter(prefix="/backtest", tags=["回测"])

# 全局实例
data_engine = DataEngine()


@router.post("/run", response_model=BacktestResponse)
async def run_backtest(request: BacktestRequest):
    """
    运行回测

    支持的策略：
    - MA_CROSS: 均线交叉策略
    - SIGNAL: 基于信号的策略
    """
    try:
        # 获取数据
        df = data_engine.get_daily_data(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date
        )

        if df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"未找到 {request.symbol} 的数据"
            )

        # 创建策略
        strategy_name = request.strategy_name.upper()

        if strategy_name == "MA_CROSS":
            strategy = MACrossStrategy(
                fast_period=request.strategy_params.get("fast_period", 5),
                slow_period=request.strategy_params.get("slow_period", 20)
            )
        elif strategy_name == "SIGNAL":
            strategy = SignalStrategy(
                signal_types=request.strategy_params.get("signal_types", None)
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的策略: {request.strategy_name}"
            )

        # 创建回测引擎
        engine = BacktestEngine(
            initial_capital=request.initial_capital
        )

        # 运行回测
        results = engine.run(
            symbol=request.symbol,
            data=df,
            strategy=strategy
        )

        # 获取绩效指标
        metrics = results["metrics"]
        portfolio = results["portfolio"]

        # 获取交易记录
        trades = []
        for trade in portfolio.trade_history:
            trades.append({
                "date": str(trade["date"]),
                "action": trade["action"],
                "symbol": trade["symbol"],
                "quantity": trade["quantity"],
                "price": trade["price"],
                "value": trade["value"],
                "commission": trade["commission"]
            })

        # 获取权益曲线
        equity_curve = []
        for snapshot in portfolio.value_history:
            equity_curve.append({
                "date": str(snapshot["date"]),
                "cash": snapshot["cash"],
                "holdings_value": snapshot["holdings_value"],
                "total_value": snapshot["total_value"],
                "returns": snapshot["returns"]
            })

        return BacktestResponse(
            strategy_name=request.strategy_name,
            symbol=request.symbol,
            period=f"{request.start_date} ~ {request.end_date}",
            initial_capital=request.initial_capital,
            final_value=metrics["final_value"],
            total_return=metrics["total_return"],
            total_return_pct=metrics["total_return_pct"],
            sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
            max_drawdown=metrics.get("max_drawdown", 0.0),
            max_drawdown_pct=metrics.get("max_drawdown_pct", 0.0),
            win_rate=metrics.get("win_rate", 0.0),
            profit_factor=metrics.get("profit_factor", 0.0),
            total_trades=metrics["total_trades"],
            winning_trades=metrics.get("winning_trades", 0),
            losing_trades=metrics.get("losing_trades", 0),
            equity_curve=equity_curve,
            trades=trades
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategies")
async def list_strategies():
    """
    获取支持的策略列表
    """
    return {
        "strategies": [
            {
                "name": "MA_CROSS",
                "display_name": "均线交叉策略",
                "description": "基于快慢均线交叉产生买卖信号",
                "parameters": {
                    "fast_period": {"type": "int", "default": 5, "description": "快线周期"},
                    "slow_period": {"type": "int", "default": 20, "description": "慢线周期"}
                }
            },
            {
                "name": "SIGNAL",
                "display_name": "信号策略",
                "description": "基于分析引擎的技术信号",
                "parameters": {
                    "signal_types": {
                        "type": "list",
                        "default": None,
                        "description": "信号类型列表（None表示使用所有信号）"
                    }
                }
            }
        ]
    }
