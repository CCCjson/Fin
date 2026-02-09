"""
历史记录API - 信号、回测、订单、成交记录
"""
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import List, Optional
from datetime import datetime, date

from data_engine.storage.history_repository import HistoryRepository
from api.models.schemas import CreateBacktestTaskRequest
from backtest_engine.backtest_executor import BacktestExecutor

router = APIRouter(prefix="/history", tags=["历史记录"])


# ==================== 信号历史 ====================

@router.get("/signals")
async def get_signals(
    symbol: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    signal_type: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0)
):
    """
    查询交易信号历史（支持分页）

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        signal_type: 信号类型 (BUY/SELL)
        strategy: 策略名称
        limit: 每页条数
        offset: 偏移量
    """
    try:
        repo = HistoryRepository()

        total = repo.count_signals(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            signal_type=signal_type,
            strategy=strategy,
        )

        signals = repo.get_signals(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            signal_type=signal_type,
            strategy=strategy,
            limit=limit,
            offset=offset,
        )

        result = []
        for signal in signals:
            result.append({
                "id": signal.id,
                "symbol": signal.symbol,
                "date": str(signal.date),
                "signal_type": signal.signal_type,
                "strength": signal.strength,
                "price": signal.price,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "position_size": signal.position_size,
                "strategy": signal.strategy,
                "signal_id": signal.signal_id,
                "created_at": signal.created_at.isoformat() if signal.created_at else None
            })

        repo.close()

        return {
            "signals": result,
            "total": total,
            "count": len(result),
            "offset": offset,
            "limit": limit
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/statistics")
async def get_signal_statistics(
    symbol: Optional[str] = None,
    days: Optional[int] = Query(default=None, ge=1, le=3650)
):
    """
    获取信号统计

    Args:
        symbol: 股票代码
        days: 统计天数（可选，不传则统计所有信号）
    """
    try:
        repo = HistoryRepository()
        stats = repo.get_signal_statistics(symbol=symbol, days=days)
        repo.close()

        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 回测历史 ====================

@router.post("/backtests")
async def create_backtest_task(
    request: CreateBacktestTaskRequest,
    background_tasks: BackgroundTasks
):
    """
    创建回测任务并自动执行

    Args:
        request: 回测任务创建请求
        background_tasks: FastAPI 后台任务
    """
    try:
        from datetime import datetime
        import uuid

        # 生成任务ID
        task_id = f"bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 保存任务
        repo = HistoryRepository()
        task = repo.save_backtest_task(
            task_id=task_id,
            name=request.name,
            strategy_type=request.strategy_type,
            symbols=request.symbols,
            start_date=datetime.strptime(request.start_date, '%Y-%m-%d').date(),
            end_date=datetime.strptime(request.end_date, '%Y-%m-%d').date(),
            initial_capital=request.initial_capital,
            strategy_params=request.strategy_params
        )

        # 在关闭 session 前提取所有需要的数据
        result = {
            "task_id": task.task_id,
            "name": task.name,
            "status": task.status,
            "strategy_type": task.strategy_type,
            "symbols": request.symbols,
            "start_date": str(task.start_date),
            "end_date": str(task.end_date),
            "initial_capital": task.initial_capital,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "message": "回测任务已创建，正在后台执行..."
        }

        repo.close()

        # 添加后台任务：自动执行回测
        executor = BacktestExecutor()
        background_tasks.add_task(executor.execute_task, task_id)

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/backtests")
async def get_backtest_tasks(
    status: Optional[str] = None,
    strategy_type: Optional[str] = None,
    limit: int = Query(default=50, le=200)
):
    """
    查询回测任务列表

    Args:
        status: 任务状态 (pending/running/completed/failed)
        strategy_type: 策略类型
        limit: 返回条数
    """
    try:
        repo = HistoryRepository()
        tasks = repo.get_backtest_tasks(
            status=status,
            strategy_type=strategy_type,
            limit=limit
        )

        result = []
        for task in tasks:
            import json
            symbols = json.loads(task.symbols) if task.symbols else []
            result.append({
                "task_id": task.task_id,
                "name": task.name,
                "status": task.status,
                "strategy_type": task.strategy_type,
                "symbols": symbols,
                "start_date": str(task.start_date),
                "end_date": str(task.end_date),
                "initial_capital": task.initial_capital,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None
            })

        repo.close()

        return {
            "tasks": result,
            "count": len(result)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/backtests/{task_id}")
async def delete_backtest_task(task_id: str):
    """
    删除回测任务

    Args:
        task_id: 回测任务ID
    """
    try:
        repo = HistoryRepository()

        # 检查任务是否存在
        tasks = repo.get_backtest_tasks(limit=1000)
        task = next((t for t in tasks if t.task_id == task_id), None)

        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

        # 删除任务（通过直接删除数据库记录）
        from data_engine.storage.models import BacktestTask, BacktestResult
        repo.session.query(BacktestResult).filter(BacktestResult.task_id == task_id).delete()
        repo.session.query(BacktestTask).filter(BacktestTask.task_id == task_id).delete()
        repo.session.commit()
        repo.close()

        return {
            "message": "任务删除成功",
            "task_id": task_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/backtests/{task_id}")
async def get_backtest_result(task_id: str):
    """
    获取回测结果详情

    Args:
        task_id: 回测任务ID
    """
    try:
        repo = HistoryRepository()
        result = repo.get_backtest_result(task_id)

        if not result:
            raise HTTPException(status_code=404, detail=f"未找到回测结果: {task_id}")

        # 获取任务信息
        tasks = repo.get_backtest_tasks(limit=1000)
        task = next((t for t in tasks if t.task_id == task_id), None)

        repo.close()

        import json

        # 解析 JSON 字段
        daily_records = json.loads(result.daily_records) if result.daily_records else []
        trade_records = json.loads(result.trade_records) if result.trade_records else []

        return {
            "task_id": result.task_id,
            "task_info": {
                "name": task.name if task else None,
                "strategy_type": task.strategy_type if task else None,
                "start_date": str(task.start_date) if task else None,
                "end_date": str(task.end_date) if task else None,
            },
            "metrics": {
                "total_return": result.total_return,
                "total_return_pct": result.total_return_pct,
                "annual_return": result.annual_return,
                "final_value": result.final_value,
                "max_drawdown": result.max_drawdown,
                "max_drawdown_pct": result.max_drawdown_pct,
                "volatility": result.volatility,
                "sharpe_ratio": result.sharpe_ratio,
                "sortino_ratio": result.sortino_ratio,
                "total_trades": result.total_trades,
                "winning_trades": result.winning_trades,
                "losing_trades": result.losing_trades,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor
            },
            "daily_records": daily_records,
            "trade_records": trade_records,
            "created_at": result.created_at.isoformat() if result.created_at else None
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/backtests/comparison")
async def compare_backtests(
    strategy_type: Optional[str] = None
):
    """
    对比多个回测结果

    Args:
        strategy_type: 策略类型（可选）
    """
    try:
        repo = HistoryRepository()
        comparison = repo.get_backtest_comparison(strategy_type=strategy_type)
        repo.close()

        return {
            "comparison": comparison,
            "count": len(comparison)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 订单历史 ====================

@router.get("/orders")
async def get_orders(
    account_id: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(default=100, le=500)
):
    """
    查询订单历史

    Args:
        account_id: 账户ID
        symbol: 股票代码
        status: 订单状态
        start_date: 开始时间
        end_date: 结束时间
        limit: 返回条数
    """
    try:
        repo = HistoryRepository()
        orders = repo.get_orders(
            account_id=account_id,
            symbol=symbol,
            status=status,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )

        result = []
        for order in orders:
            result.append({
                "order_id": order.order_id,
                "account_id": order.account_id,
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "quantity": order.quantity,
                "price": order.price,
                "status": order.status,
                "filled_quantity": order.filled_quantity,
                "avg_fill_price": order.avg_fill_price,
                "commission": order.commission,
                "strategy": order.strategy,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "filled_at": order.filled_at.isoformat() if order.filled_at else None
            })

        repo.close()

        return {
            "orders": result,
            "count": len(result)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders/statistics")
async def get_order_statistics(
    account_id: str,
    days: int = Query(default=30, ge=1, le=365)
):
    """
    获取订单统计

    Args:
        account_id: 账户ID
        days: 统计天数
    """
    try:
        repo = HistoryRepository()
        stats = repo.get_order_statistics(account_id=account_id, days=days)
        repo.close()

        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 成交历史 ====================

@router.get("/trades")
async def get_trades(
    account_id: Optional[str] = None,
    symbol: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(default=100, le=500)
):
    """
    查询成交记录

    Args:
        account_id: 账户ID
        symbol: 股票代码
        start_date: 开始时间
        end_date: 结束时间
        limit: 返回条数
    """
    try:
        repo = HistoryRepository()
        trades = repo.get_trades(
            account_id=account_id,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )

        result = []
        for trade in trades:
            result.append({
                "trade_id": trade.trade_id,
                "order_id": trade.order_id,
                "account_id": trade.account_id,
                "symbol": trade.symbol,
                "direction": trade.direction,
                "quantity": trade.quantity,
                "price": trade.price,
                "commission": trade.commission,
                "slippage": trade.slippage,
                "amount": trade.amount,
                "executed_at": trade.executed_at.isoformat() if trade.executed_at else None
            })

        repo.close()

        return {
            "trades": result,
            "count": len(result)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades/statistics")
async def get_trade_statistics(
    account_id: str,
    days: int = Query(default=30, ge=1, le=365)
):
    """
    获取成交统计

    Args:
        account_id: 账户ID
        days: 统计天数
    """
    try:
        repo = HistoryRepository()
        stats = repo.get_trade_statistics(account_id=account_id, days=days)
        repo.close()

        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
