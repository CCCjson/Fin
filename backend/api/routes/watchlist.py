"""
自选股 + 价格预警 API
"""
import uuid
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo
from data_engine.storage.repository import WatchlistRepository, PriceAlertRepository

router = APIRouter(prefix="/watchlist", tags=["自选股与预警"])


# ==================== 请求模型 ====================

class WatchlistAdd(BaseModel):
    symbol: str = Field(..., description="股票代码，如 600519.SH")
    name: Optional[str] = Field(None, description="名称，不填自动查")
    group_name: str = Field("默认分组", description="分组")
    note: Optional[str] = Field(None, description="备注")


class WatchlistUpdate(BaseModel):
    name: Optional[str] = None
    group_name: Optional[str] = None
    note: Optional[str] = None
    sort_order: Optional[int] = None


class AlertCreate(BaseModel):
    symbol: str = Field(..., description="股票代码")
    alert_type: str = Field(..., description="price_above / price_below / pct_change")
    threshold: float = Field(..., description="阈值（价格 或 涨跌幅%）")
    name: Optional[str] = None
    repeat: int = Field(0, description="0=仅触发一次 1=反复触发")
    message: Optional[str] = None


# ==================== 自选股 ====================

@router.get("", summary="自选股列表（按分组）")
async def list_watchlist():
    session = get_session()
    try:
        items = WatchlistRepository(session).list_all()
        data = [
            {"id": w.id, "symbol": w.symbol, "name": w.name,
             "group_name": w.group_name, "note": w.note, "sort_order": w.sort_order}
            for w in items
        ]
        return {"success": True, "data": data}
    finally:
        session.close()


@router.get("/groups", summary="自选股分组列表")
async def list_groups():
    session = get_session()
    try:
        return {"success": True, "data": WatchlistRepository(session).list_groups()}
    finally:
        session.close()


@router.post("", summary="添加自选股")
async def add_watchlist(req: WatchlistAdd):
    session = get_session()
    try:
        name = req.name
        if not name:
            info = session.query(StockInfo).filter(StockInfo.symbol == req.symbol).first()
            name = info.name if info else None
        w = WatchlistRepository(session).add(req.symbol, name, req.group_name, req.note)
        return {"success": True, "data": {"id": w.id, "symbol": w.symbol, "name": w.name,
                                          "group_name": w.group_name}}
    except Exception as e:
        logger.error(f"添加自选股失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
    finally:
        session.close()


@router.put("/{item_id}", summary="修改自选股（分组/备注/排序）")
async def update_watchlist(item_id: int, req: WatchlistUpdate):
    session = get_session()
    try:
        w = WatchlistRepository(session).update(item_id, **req.model_dump())
        if not w:
            return JSONResponse(status_code=404, content={"success": False, "error": "不存在"})
        return {"success": True}
    finally:
        session.close()


@router.delete("/{item_id}", summary="删除自选股")
async def delete_watchlist(item_id: int):
    session = get_session()
    try:
        ok = WatchlistRepository(session).delete(item_id)
        if not ok:
            return JSONResponse(status_code=404, content={"success": False, "error": "不存在"})
        return {"success": True}
    finally:
        session.close()


# ==================== 价格预警 ====================

@router.get("/alerts", summary="预警列表")
async def list_alerts(status: Optional[str] = None):
    session = get_session()
    try:
        items = PriceAlertRepository(session).list_all(status)
        data = [
            {"alert_id": a.alert_id, "symbol": a.symbol, "name": a.name,
             "alert_type": a.alert_type, "threshold": a.threshold, "status": a.status,
             "repeat": a.repeat, "triggered_at": str(a.triggered_at) if a.triggered_at else None,
             "triggered_price": a.triggered_price, "message": a.message}
            for a in items
        ]
        return {"success": True, "data": data}
    finally:
        session.close()


@router.post("/alerts", summary="创建预警")
async def create_alert(req: AlertCreate):
    if req.alert_type not in ("price_above", "price_below", "pct_change"):
        return JSONResponse(status_code=400, content={"success": False, "error": "alert_type 非法"})
    session = get_session()
    try:
        name = req.name
        if not name:
            info = session.query(StockInfo).filter(StockInfo.symbol == req.symbol).first()
            name = info.name if info else None
        alert_id = f"pa_{uuid.uuid4().hex[:12]}"
        a = PriceAlertRepository(session).add(
            alert_id, req.symbol, req.alert_type, req.threshold,
            name=name, repeat=req.repeat, message=req.message,
        )
        return {"success": True, "data": {"alert_id": a.alert_id}}
    except Exception as e:
        logger.error(f"创建预警失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
    finally:
        session.close()


@router.put("/alerts/{alert_id}", summary="更新预警状态（暂停/恢复）")
async def update_alert(alert_id: str, status: str):
    if status not in ("active", "paused"):
        return JSONResponse(status_code=400, content={"success": False, "error": "status 非法"})
    session = get_session()
    try:
        a = PriceAlertRepository(session).update_status(alert_id, status)
        if not a:
            return JSONResponse(status_code=404, content={"success": False, "error": "不存在"})
        return {"success": True}
    finally:
        session.close()


@router.delete("/alerts/{alert_id}", summary="删除预警")
async def delete_alert(alert_id: str):
    session = get_session()
    try:
        ok = PriceAlertRepository(session).delete(alert_id)
        if not ok:
            return JSONResponse(status_code=404, content={"success": False, "error": "不存在"})
        return {"success": True}
    finally:
        session.close()
