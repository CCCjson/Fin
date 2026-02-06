"""
监控相关API
"""
from fastapi import APIRouter, HTTPException
from typing import Optional

from api.models.schemas import (
    TradeLogResponse,
    OrderStatusResponse,
    PerformanceResponse,
    AlertResponse,
    MessageResponse
)
from trading_engine.monitor import (
    OrderTracker,
    TradeLogger,
    PerformanceMonitor,
    AlertManager,
    AlertType
)

router = APIRouter(prefix="/monitor", tags=["监控"])

# 全局监控实例
order_tracker: Optional[OrderTracker] = None
trade_logger: Optional[TradeLogger] = None
performance_monitor: Optional[PerformanceMonitor] = None
alert_manager: Optional[AlertManager] = None


def get_order_tracker() -> OrderTracker:
    """获取订单跟踪器"""
    global order_tracker
    if order_tracker is None:
        raise HTTPException(
            status_code=400,
            detail="监控模块未初始化，请先调用 /monitor/init"
        )
    return order_tracker


def get_trade_logger() -> TradeLogger:
    """获取交易日志记录器"""
    global trade_logger
    if trade_logger is None:
        raise HTTPException(
            status_code=400,
            detail="监控模块未初始化，请先调用 /monitor/init"
        )
    return trade_logger


def get_performance_monitor() -> PerformanceMonitor:
    """获取绩效监控器"""
    global performance_monitor
    if performance_monitor is None:
        raise HTTPException(
            status_code=400,
            detail="监控模块未初始化，请先调用 /monitor/init"
        )
    return performance_monitor


def get_alert_manager() -> AlertManager:
    """获取告警管理器"""
    global alert_manager
    if alert_manager is None:
        raise HTTPException(
            status_code=400,
            detail="监控模块未初始化，请先调用 /monitor/init"
        )
    return alert_manager


@router.post("/init", response_model=MessageResponse)
async def init_monitoring(initial_capital: float = 1000000.0):
    """
    初始化监控模块

    Args:
        initial_capital: 初始资金
    """
    global order_tracker, trade_logger, performance_monitor, alert_manager

    try:
        order_tracker = OrderTracker()
        trade_logger = TradeLogger()
        performance_monitor = PerformanceMonitor(initial_capital=initial_capital)
        alert_manager = AlertManager()

        return MessageResponse(
            message="监控模块初始化成功",
            success=True,
            data={"initial_capital": initial_capital}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades", response_model=TradeLogResponse)
async def get_trade_logs(
    symbol: Optional[str] = None,
    limit: int = 100
):
    """
    获取交易日志

    Args:
        symbol: 股票代码（可选）
        limit: 返回条数
    """
    logger = get_trade_logger()

    try:
        trades = logger.get_trades(symbol=symbol)
        if limit:
            trades = trades[-limit:]

        statistics = logger.get_statistics()

        # 转换时间戳
        trades_list = []
        for trade in trades:
            trade_copy = trade.copy()
            if "timestamp" in trade_copy:
                trade_copy["timestamp"] = str(trade_copy["timestamp"])
            trades_list.append(trade_copy)

        return TradeLogResponse(
            trades=trades_list,
            count=len(trades_list),
            statistics=statistics
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders/status", response_model=OrderStatusResponse)
async def get_order_status(
    symbol: Optional[str] = None,
    status: Optional[str] = None
):
    """
    获取订单状态

    Args:
        symbol: 股票代码（可选）
        status: 订单状态（可选）
    """
    tracker = get_order_tracker()

    try:
        # 获取订单
        if symbol:
            orders = tracker.get_orders_by_symbol(symbol)
        else:
            orders = list(tracker.orders.values())

        # 转换为dict
        orders_list = []
        for order in orders:
            orders_list.append({
                "order_id": order.order_id,
                "symbol": order.symbol,
                "action": order.action,
                "quantity": order.quantity,
                "price": order.price,
                "status": order.status.value,
                "filled_price": order.filled_price,
                "filled_quantity": order.filled_quantity,
                "commission": order.commission,
                "submit_time": order.submit_time.isoformat()
            })

        statistics = tracker.get_statistics()

        return OrderStatusResponse(
            orders=orders_list,
            count=len(orders_list),
            statistics=statistics
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/performance", response_model=PerformanceResponse)
async def get_performance():
    """
    获取绩效统计
    """
    monitor = get_performance_monitor()

    try:
        current_performance = monitor.get_current_performance()
        statistics = monitor.get_statistics()
        equity_curve = monitor.get_equity_curve()
        drawdown_curve = monitor.get_drawdown_curve()

        # 转换时间戳
        for item in equity_curve:
            if "timestamp" in item:
                item["timestamp"] = str(item["timestamp"])

        for item in drawdown_curve:
            if "timestamp" in item:
                item["timestamp"] = str(item["timestamp"])

        if "timestamp" in current_performance:
            current_performance["timestamp"] = str(current_performance["timestamp"])

        return PerformanceResponse(
            current_performance=current_performance,
            statistics=statistics,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts", response_model=AlertResponse)
async def get_alerts(
    alert_type: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    limit: int = 100
):
    """
    获取告警列表

    Args:
        alert_type: 告警类型（INFO/WARNING/ERROR/CRITICAL）
        acknowledged: 是否已确认
        limit: 返回条数
    """
    manager = get_alert_manager()

    try:
        # 转换alert_type
        type_filter = None
        if alert_type:
            try:
                type_filter = AlertType[alert_type.upper()]
            except KeyError:
                raise HTTPException(
                    status_code=400,
                    detail=f"无效的告警类型: {alert_type}"
                )

        alerts = manager.get_alerts(
            alert_type=type_filter,
            acknowledged=acknowledged,
            limit=limit
        )

        # 转换为dict
        alerts_list = []
        for alert in alerts:
            alerts_list.append({
                "alert_type": alert.alert_type.value,
                "title": alert.title,
                "message": alert.message,
                "data": alert.data,
                "timestamp": alert.timestamp.isoformat(),
                "acknowledged": alert.acknowledged
            })

        statistics = manager.get_statistics()

        # 转换statistics中的AlertType为字符串
        statistics_converted = statistics.copy()
        if "by_type" in statistics_converted:
            by_type_converted = {}
            for k, v in statistics_converted["by_type"].items():
                by_type_converted[k.value] = v
            statistics_converted["by_type"] = by_type_converted

        return AlertResponse(
            alerts=alerts_list,
            count=len(alerts_list),
            statistics=statistics_converted
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset", response_model=MessageResponse)
async def reset_monitoring():
    """
    重置监控模块
    """
    global order_tracker, trade_logger, performance_monitor, alert_manager

    order_tracker = None
    trade_logger = None
    performance_monitor = None
    alert_manager = None

    return MessageResponse(
        message="监控模块已重置",
        success=True
    )
