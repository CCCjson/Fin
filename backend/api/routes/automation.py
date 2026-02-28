"""
自动化交易 API — 待确认订单 / 配置 / 调度器 / 日志
"""
import json
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Query, Body
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import PendingOrder, AutomationConfig, AutomationLog
from automation.pending_order_manager import PendingOrderManager
from automation.websocket_manager import (
    notify_order_status,
    notify_scheduler_status,
)

router = APIRouter(prefix="/automation", tags=["自动化交易"])

# 全局管理器
_order_manager = PendingOrderManager()


# ==================== 待确认订单 ====================

@router.get("/pending-orders")
async def list_pending_orders(
    status: Optional[str] = Query(None, description="筛选状态: PENDING/FILLED/REJECTED/EXPIRED/FAILED"),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """获取待确认订单列表"""
    orders = _order_manager.get_pending_orders(status=status, symbol=symbol, limit=limit)
    return {"success": True, "orders": orders, "total": len(orders)}


@router.get("/pending-orders/{order_id}")
async def get_pending_order(order_id: str):
    """获取单个订单详情"""
    order = _order_manager.get_order_detail(order_id)
    if not order:
        return {"success": False, "message": f"订单 {order_id} 不存在"}
    return {"success": True, "order": order}


@router.post("/pending-orders/{order_id}/confirm")
async def confirm_order(
    order_id: str,
    broker_type: Optional[str] = Body(None),
    override_qty: Optional[int] = Body(None),
    override_price: Optional[float] = Body(None),
):
    """确认并执行订单"""
    result = _order_manager.confirm_order(
        order_id=order_id,
        broker_type=broker_type,
        override_qty=override_qty,
        override_price=override_price,
    )

    # WebSocket 通知
    if result.get("order"):
        status = result["order"].get("status", "")
        try:
            await notify_order_status(order_id, status, result["order"])
        except Exception as e:
            logger.debug(f"WS 通知失败: {e}")

    return result


@router.post("/pending-orders/{order_id}/reject")
async def reject_order(
    order_id: str,
    reason: str = Body("", embed=True),
):
    """拒绝订单"""
    result = _order_manager.reject_order(order_id=order_id, reason=reason)

    if result.get("order"):
        try:
            await notify_order_status(order_id, "REJECTED", result["order"])
        except Exception as e:
            logger.debug(f"WS 通知失败: {e}")

    return result


@router.post("/pending-orders/batch-confirm")
async def batch_confirm(
    order_ids: List[str] = Body(..., embed=True),
):
    """批量确认订单"""
    results = []
    for oid in order_ids:
        r = _order_manager.confirm_order(order_id=oid)
        results.append({"order_id": oid, **r})

        if r.get("order"):
            try:
                await notify_order_status(oid, r["order"].get("status", ""), r["order"])
            except Exception:
                pass

    success_count = sum(1 for r in results if r.get("success"))
    return {
        "success": True,
        "total": len(order_ids),
        "confirmed": success_count,
        "failed": len(order_ids) - success_count,
        "results": results,
    }


@router.post("/pending-orders/batch-reject")
async def batch_reject(
    order_ids: List[str] = Body(..., embed=True),
    reason: str = Body("批量拒绝", embed=True),
):
    """批量拒绝订单"""
    results = []
    for oid in order_ids:
        r = _order_manager.reject_order(order_id=oid, reason=reason)
        results.append({"order_id": oid, **r})

    success_count = sum(1 for r in results if r.get("success"))
    return {
        "success": True,
        "total": len(order_ids),
        "rejected": success_count,
        "results": results,
    }


# ==================== 自动化配置 ====================

@router.get("/configs")
async def list_configs():
    """获取所有自动化配置"""
    session = get_session()
    try:
        configs = session.query(AutomationConfig).order_by(AutomationConfig.id).all()
        result = []
        for c in configs:
            result.append(_config_to_dict(c))
        return {"success": True, "configs": result}
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        return {"success": False, "message": str(e)}
    finally:
        session.close()


@router.post("/configs")
async def create_config(
    name: str = Body(...),
    scan_type: str = Body(...),
    frequency_minutes: int = Body(5),
    strategies: List[str] = Body(default=["MACD", "KDJ", "RSI", "MA"]),
    watchlist: List[str] = Body(default=[]),
    min_strength: float = Body(0.6),
    broker_type: str = Body("paper"),
    position_size_pct: float = Body(0.10),
    order_expire_minutes: int = Body(30),
):
    """创建自动化配置"""
    session = get_session()
    try:
        import uuid
        config_id = f"AC-{uuid.uuid4().hex[:8]}"
        config = AutomationConfig(
            config_id=config_id,
            name=name,
            enabled=1,
            scan_type=scan_type,
            frequency_minutes=frequency_minutes,
            strategies=json.dumps(strategies, ensure_ascii=False),
            watchlist=json.dumps(watchlist, ensure_ascii=False),
            min_strength=min_strength,
            broker_type=broker_type,
            position_size_pct=position_size_pct,
            order_expire_minutes=order_expire_minutes,
        )
        session.add(config)
        session.commit()

        return {"success": True, "config": _config_to_dict(config)}
    except Exception as e:
        session.rollback()
        logger.error(f"创建配置失败: {e}")
        return {"success": False, "message": str(e)}
    finally:
        session.close()


@router.put("/configs/{config_id}")
async def update_config(config_id: str, updates: dict = Body(...)):
    """更新自动化配置"""
    session = get_session()
    try:
        config = session.query(AutomationConfig).filter(AutomationConfig.config_id == config_id).first()
        if not config:
            return {"success": False, "message": f"配置 {config_id} 不存在"}

        # 可更新的字段
        updatable = ["name", "scan_type", "frequency_minutes", "min_strength",
                      "broker_type", "position_size_pct", "order_expire_minutes"]
        for key in updatable:
            if key in updates:
                setattr(config, key, updates[key])

        if "strategies" in updates:
            config.strategies = json.dumps(updates["strategies"], ensure_ascii=False)
        if "watchlist" in updates:
            config.watchlist = json.dumps(updates["watchlist"], ensure_ascii=False)

        session.commit()
        return {"success": True, "config": _config_to_dict(config)}
    except Exception as e:
        session.rollback()
        logger.error(f"更新配置失败: {e}")
        return {"success": False, "message": str(e)}
    finally:
        session.close()


@router.delete("/configs/{config_id}")
async def delete_config(config_id: str):
    """删除自动化配置"""
    session = get_session()
    try:
        config = session.query(AutomationConfig).filter(AutomationConfig.config_id == config_id).first()
        if not config:
            return {"success": False, "message": f"配置 {config_id} 不存在"}

        session.delete(config)
        session.commit()
        return {"success": True, "message": f"配置 {config_id} 已删除"}
    except Exception as e:
        session.rollback()
        logger.error(f"删除配置失败: {e}")
        return {"success": False, "message": str(e)}
    finally:
        session.close()


@router.post("/configs/{config_id}/toggle")
async def toggle_config(config_id: str):
    """启用/禁用配置"""
    session = get_session()
    try:
        config = session.query(AutomationConfig).filter(AutomationConfig.config_id == config_id).first()
        if not config:
            return {"success": False, "message": f"配置 {config_id} 不存在"}

        config.enabled = 0 if config.enabled else 1
        session.commit()
        return {
            "success": True,
            "enabled": bool(config.enabled),
            "message": f"配置已{'启用' if config.enabled else '禁用'}",
        }
    except Exception as e:
        session.rollback()
        logger.error(f"切换配置失败: {e}")
        return {"success": False, "message": str(e)}
    finally:
        session.close()


# ==================== 调度器 ====================

@router.post("/scheduler/start")
async def start_scheduler():
    """启动调度器"""
    from automation.scheduler import automation_scheduler
    try:
        automation_scheduler.start()
        await notify_scheduler_status(True, "调度器已启动")
        return {"success": True, "message": "调度器已启动", "running": True}
    except Exception as e:
        logger.error(f"启动调度器失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/scheduler/stop")
async def stop_scheduler():
    """停止调度器"""
    from automation.scheduler import automation_scheduler
    try:
        automation_scheduler.stop()
        await notify_scheduler_status(False, "调度器已停止")
        return {"success": True, "message": "调度器已停止", "running": False}
    except Exception as e:
        logger.error(f"停止调度器失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/scheduler/status")
async def scheduler_status():
    """获取调度器状态"""
    from automation.scheduler import automation_scheduler
    return {
        "success": True,
        "running": automation_scheduler.is_running,
        "jobs": automation_scheduler.get_jobs_info(),
    }


@router.post("/scheduler/trigger/{config_id}")
async def trigger_scan(config_id: str):
    """手动触发一次扫描"""
    from automation.scheduler import automation_scheduler
    try:
        result = await automation_scheduler.trigger_scan(config_id)
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"手动触发失败: {e}")
        return {"success": False, "message": str(e)}


# ==================== 日志与统计 ====================

@router.get("/logs")
async def list_logs(
    config_id: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    """获取运行日志"""
    session = get_session()
    try:
        query = session.query(AutomationLog)
        if config_id:
            query = query.filter(AutomationLog.config_id == config_id)

        logs = query.order_by(AutomationLog.started_at.desc()).limit(limit).all()
        result = []
        for log in logs:
            result.append({
                "id": log.id,
                "config_id": log.config_id,
                "run_type": log.run_type,
                "status": log.status,
                "symbols_scanned": log.symbols_scanned,
                "signals_found": log.signals_found,
                "orders_created": log.orders_created,
                "duration_seconds": log.duration_seconds,
                "error_message": log.error_message,
                "detail": json.loads(log.detail) if log.detail else None,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            })
        return {"success": True, "logs": result}
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        return {"success": False, "message": str(e)}
    finally:
        session.close()


@router.get("/statistics")
async def get_statistics():
    """获取统计数据"""
    stats = _order_manager.get_statistics()
    return {"success": True, **stats}


# -------- 工具函数 --------

def _config_to_dict(c: AutomationConfig) -> dict:
    """AutomationConfig 转字典"""
    def _parse(text):
        if not text:
            return []
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []

    return {
        "config_id": c.config_id,
        "name": c.name,
        "enabled": bool(c.enabled),
        "scan_type": c.scan_type,
        "frequency_minutes": c.frequency_minutes,
        "strategies": _parse(c.strategies),
        "watchlist": _parse(c.watchlist),
        "min_strength": c.min_strength,
        "broker_type": c.broker_type,
        "position_size_pct": c.position_size_pct,
        "order_expire_minutes": c.order_expire_minutes,
        "last_run_at": c.last_run_at.isoformat() if c.last_run_at else None,
        "next_run_at": c.next_run_at.isoformat() if c.next_run_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
