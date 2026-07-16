"""
自动化交易 API — 待确认订单 / 配置 / 调度器 / 日志
"""
import asyncio
import json
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Query, Body
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import PendingOrder, AutomationConfig, AutomationLog, ManualTrade
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
    def _work():
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
    return await asyncio.to_thread(_work)


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
    def _work():
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
    return await asyncio.to_thread(_work)


@router.put("/configs/{config_id}")
async def update_config(config_id: str, updates: dict = Body(...)):
    """更新自动化配置"""
    def _work():
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
    return await asyncio.to_thread(_work)


@router.delete("/configs/{config_id}")
async def delete_config(config_id: str):
    """删除自动化配置"""
    def _work():
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
    return await asyncio.to_thread(_work)


@router.post("/configs/{config_id}/toggle")
async def toggle_config(config_id: str):
    """启用/禁用配置"""
    def _work():
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
    return await asyncio.to_thread(_work)


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
    def _work():
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
    return await asyncio.to_thread(_work)


@router.get("/statistics")
async def get_statistics():
    """获取统计数据"""
    stats = _order_manager.get_statistics()
    return {"success": True, **stats}


# ==================== 交易中心扩展 API ====================

@router.get("/broker-status")
async def get_broker_status():
    """获取各 broker 连接状态 + 账户摘要"""
    results = {}

    # Paper Broker — 始终在线
    try:
        from automation.pending_order_manager import get_paper_broker
        paper = get_paper_broker()
        info = paper.get_account_info()
        results["paper"] = {
            "online": True,
            "name": "模拟交易",
            "cash": info.get("cash", 0),
            "total_value": info.get("total_value", 0),
            "market_value": info.get("market_value", 0),
            "unrealized_pnl": info.get("unrealized_pnl", 0),
            "return_pct": info.get("return_pct", 0),
            "total_trades": info.get("total_trades", 0),
        }
    except Exception as e:
        logger.error(f"获取 PaperBroker 状态失败: {e}")
        results["paper"] = {"online": False, "name": "模拟交易", "error": str(e)}

    # EasyTrader — 尚未接入，直接标记离线（后续接入后改为实际探测）
    results["easytrader"] = {"online": False, "name": "EasyTrader"}

    # 汇总
    total_value = sum(b.get("total_value", 0) for b in results.values() if b.get("online"))
    total_cash = sum(b.get("cash", 0) for b in results.values() if b.get("online"))
    total_pnl = sum(b.get("unrealized_pnl", 0) for b in results.values() if b.get("online"))

    return {
        "success": True,
        "brokers": results,
        "summary": {
            "total_value": total_value,
            "total_cash": total_cash,
            "total_pnl": total_pnl,
        },
    }


@router.get("/broker-positions")
async def get_broker_positions(
    broker_type: str = Query("paper", description="券商类型: paper/easytrader"),
):
    """获取指定 broker 的持仓"""
    try:
        positions = []

        if broker_type == "paper":
            from automation.pending_order_manager import get_paper_broker
            broker = get_paper_broker()
            for pos in broker.get_positions():
                positions.append({
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "avg_cost": round(pos.avg_cost, 3),
                    "current_price": round(pos.current_price, 3),
                    "market_value": round(pos.market_value, 2),
                    "unrealized_pnl": round(pos.unrealized_pnl, 2),
                    "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                    "available": pos.available,
                    "broker": "paper",
                })

        elif broker_type == "easytrader":
            from trading_engine.brokers.easytrader_broker import EasyTraderBroker
            broker = EasyTraderBroker()
            if not broker.connect():
                return {"success": False, "message": "EasyTrader 未连接"}
            for pos in broker.get_positions():
                positions.append({
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "avg_cost": round(pos.avg_cost, 3),
                    "current_price": round(pos.current_price, 3),
                    "market_value": round(pos.market_value, 2),
                    "unrealized_pnl": round(pos.unrealized_pnl, 2),
                    "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                    "available": pos.available,
                    "broker": "easytrader",
                })

        else:
            return {"success": False, "message": f"不支持的券商类型: {broker_type}"}

        # 批量补充股票名称
        from data_engine.storage.repository import get_stock_names
        session = get_session()
        try:
            names = get_stock_names(session, [p["symbol"] for p in positions])
        finally:
            session.close()
        for p in positions:
            p["name"] = names.get(p["symbol"], p["symbol"])

        return {"success": True, "positions": positions, "total": len(positions)}

    except Exception as e:
        logger.error(f"获取持仓失败 ({broker_type}): {e}")
        return {"success": False, "message": str(e)}


@router.post("/broker-order")
async def submit_broker_order(
    broker_type: str = Body(...),
    symbol: str = Body(...),
    action: str = Body(...),
    quantity: int = Body(...),
    price: Optional[float] = Body(None),
):
    """统一手动下单入口"""
    from automation.pending_order_manager import get_paper_broker
    from trading_engine.risk.manager import RiskManager
    from trading_engine.risk.adapter import build_broker_info, get_total_capital, get_effective_risk_config

    # 风控检查
    try:
        risk_mgr = RiskManager(get_effective_risk_config())
        total_capital = get_total_capital()
        broker_info = build_broker_info(total_capital)
        risk_passed, risk_results = risk_mgr.check_order(
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price or 0,
            broker_info=broker_info,
        )
        if not risk_passed:
            failed_rules = [r.message for r in risk_results if not r.passed]
            return {"success": False, "message": f"风控检查未通过: {'; '.join(failed_rules)}"}
    except Exception as e:
        logger.warning(f"风控检查异常（允许继续）: {e}")

    # 执行交易
    try:
        if broker_type == "paper":
            broker = get_paper_broker()
            if price:
                broker.update_market_price(symbol, price)
            broker_order = broker.submit_order(symbol, action, quantity, price)

        elif broker_type == "easytrader":
            from trading_engine.brokers.easytrader_broker import EasyTraderBroker
            broker = EasyTraderBroker()
            if not broker.connect():
                return {"success": False, "message": "EasyTrader 未连接"}
            broker_order = broker.submit_order(symbol, action, quantity, price)

        else:
            return {"success": False, "message": f"不支持的券商类型: {broker_type}"}

        # 检查执行结果
        from trading_engine.brokers.base import OrderStatus as BOS
        if broker_order.status in (BOS.FILLED, BOS.SUBMITTED):
            # 写入 ManualTrade 表
            try:
                session = get_session()
                exec_price = broker_order.filled_price or price or 0
                exec_qty = broker_order.filled_quantity or quantity
                trade = ManualTrade(
                    symbol=symbol,
                    name=symbol,
                    side=action,
                    price=exec_price,
                    quantity=exec_qty,
                    amount=exec_price * exec_qty,
                    commission=broker_order.commission,
                    trade_date=datetime.now().date(),
                    note=f"[手动下单] broker={broker_type}",
                    source_type="manual_broker",
                )
                session.add(trade)
                session.commit()
                session.close()
            except Exception as e:
                logger.error(f"写入 ManualTrade 失败: {e}")

            return {
                "success": True,
                "message": f"{action} {symbol} x{quantity} 成功",
                "order": {
                    "order_id": broker_order.order_id,
                    "status": broker_order.status.value,
                    "filled_price": broker_order.filled_price,
                    "filled_quantity": broker_order.filled_quantity,
                    "commission": broker_order.commission,
                },
            }
        else:
            return {
                "success": False,
                "message": broker_order.error_msg or f"下单失败: {broker_order.status.value}",
            }

    except Exception as e:
        logger.error(f"手动下单失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/execution-history")
async def get_execution_history(
    status: Optional[str] = Query(None, description="FILLED/REJECTED/EXPIRED/FAILED"),
    broker_type: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
):
    """查询执行历史（非 PENDING 的 PendingOrder 记录）"""
    def _work():
        session = get_session()
        try:
            query = session.query(PendingOrder).filter(PendingOrder.status != "PENDING")

            if status:
                query = query.filter(PendingOrder.status == status)
            if broker_type:
                query = query.filter(PendingOrder.broker_type == broker_type)
            if symbol:
                query = query.filter(PendingOrder.symbol.contains(symbol))
            if date_from:
                query = query.filter(PendingOrder.created_at >= datetime.fromisoformat(date_from))
            if date_to:
                query = query.filter(PendingOrder.created_at <= datetime.fromisoformat(date_to + "T23:59:59"))

            orders = query.order_by(PendingOrder.updated_at.desc()).limit(limit).all()

            result = []
            for o in orders:
                result.append({
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "name": o.name,
                    "signal_type": o.signal_type,
                    "strategy": o.strategy,
                    "strength": o.strength,
                    "suggested_price": o.suggested_price,
                    "suggested_quantity": o.suggested_quantity,
                    "actual_price": o.actual_price,
                    "actual_quantity": o.actual_quantity,
                    "commission": o.commission,
                    "status": o.status,
                    "broker_type": o.broker_type,
                    "scan_source": o.scan_source,
                    "reject_reason": o.reject_reason,
                    "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                    "updated_at": o.updated_at.isoformat() if o.updated_at else None,
                })

            return {"success": True, "orders": result, "total": len(result)}

        except Exception as e:
            logger.error(f"获取执行历史失败: {e}")
            return {"success": False, "message": str(e)}
        finally:
            session.close()
    return await asyncio.to_thread(_work)


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
