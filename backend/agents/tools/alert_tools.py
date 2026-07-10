"""
价格预警类工具 —— 包 PriceAlertRepository 的预警 CRUD。

创建/删除走二次确认；预警入库后由 automation/price_alert_monitor 盘中轮询触发，
无需额外激活。alert_type: price_above / price_below / pct_change。
"""
import uuid
from typing import Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo
from data_engine.storage.repository import PriceAlertRepository

_TYPE_LABEL = {"price_above": "涨到", "price_below": "跌到", "pct_change": "涨跌幅达"}


def _stock_name(symbol: str) -> Optional[str]:
    session = get_session()
    try:
        info = session.query(StockInfo).filter(StockInfo.symbol == symbol).first()
        return info.name if info else None
    finally:
        session.close()


def _alert_dict(a) -> dict:
    return {
        "alert_id": a.alert_id, "symbol": a.symbol, "name": a.name,
        "alert_type": a.alert_type, "threshold": a.threshold, "status": a.status,
        "repeat": a.repeat, "message": a.message,
        "triggered_at": str(a.triggered_at) if a.triggered_at else None,
        "triggered_price": a.triggered_price,
    }


class ListPriceAlertsArgs(BaseModel):
    status: Optional[Literal["active", "paused", "triggered"]] = Field(
        None, description="可选：按状态过滤")


@tool(
    name="list_price_alerts",
    description="查看已设置的价格预警列表（含状态、阈值、是否已触发）。用户问「我设了哪些提醒/预警」时调用。",
    args_model=ListPriceAlertsArgs,
    category="monitor",
    group="watchlist_alerts",
)
def list_price_alerts(status: Optional[str] = None) -> ToolEnvelope:
    session = get_session()
    try:
        items = PriceAlertRepository(session).list_all(status)
        alerts = [_alert_dict(a) for a in items]
    finally:
        session.close()
    if not alerts:
        return ToolEnvelope(
            business_result="negative",
            message="当前没有" + (f"{status} 状态的" if status else "") + "价格预警。")
    return ToolEnvelope(data={"count": len(alerts), "alerts": alerts})


class CheckPriceAlertsArgs(BaseModel):
    pass


@tool(
    name="check_price_alerts",
    description=(
        "立刻检查所有 active 价格预警是否触发（现拉一次实时价对比阈值，触发的会自动改状态）。"
        "用户问「我的预警触发了吗」「有没有到价」时调用。没有 active 预警时不出网。"
    ),
    args_model=CheckPriceAlertsArgs,
    category="monitor",
    group="watchlist_alerts",
)
def check_price_alerts() -> ToolEnvelope:
    """按需扫描。以前靠 automation/price_alert_monitor 盘中每 30s 常驻轮询，
    没有开关也没人看，白烧快代理额度（Jason 2026-07-10 拍板改按需）。"""
    from automation.price_alert_monitor import _scan_once

    events = _scan_once()
    if not events:
        return ToolEnvelope(
            business_result="negative",
            message="检查完毕，当前没有预警触发（或没有 active 预警）。")
    hits = [{
        "symbol": e["data"]["symbol"], "name": e["data"].get("name"),
        "alert_type": e["data"].get("alert_type"),
        "threshold": e["data"].get("threshold"),
        "price": e["data"].get("price"),
        "message": e["data"].get("message"),
    } for e in events]
    return ToolEnvelope(data={"triggered_count": len(hits), "triggered": hits})


def preview_create_alert(args: dict) -> dict:
    """确认前预览：预警内容 + 当前价参照。"""
    symbol = (args.get("symbol") or "").strip()
    alert_type = args.get("alert_type")
    threshold = args.get("threshold")
    if not symbol or alert_type not in _TYPE_LABEL or threshold is None:
        return {"error": "缺少 symbol/alert_type/threshold 或类型非法"}
    from agents.tools.trading_tools import _resolve_price
    current = _resolve_price(symbol)
    unit = "%" if alert_type == "pct_change" else "元"
    return {
        "symbol": symbol, "name": _stock_name(symbol),
        "alert_type": alert_type, "threshold": threshold,
        "current_price": current,
        "repeat": "反复触发" if args.get("repeat") else "仅一次",
        "desc": f"{symbol} {_TYPE_LABEL[alert_type]} {threshold}{unit} 时提醒",
    }


class CreatePriceAlertArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    alert_type: Literal["price_above", "price_below", "pct_change"] = Field(
        ..., description="price_above=涨到价位 price_below=跌到价位 pct_change=当日涨跌幅达(%)")
    threshold: float = Field(..., description="阈值：价格(元) 或 涨跌幅(%)")
    repeat: bool = Field(False, description="是否反复触发，默认否（触发一次即完成）")
    message: Optional[str] = Field(None, description="可选：触发时附带的提醒语")


@tool(
    name="create_price_alert",
    description=(
        "创建价格预警：股价涨到/跌到某价位，或涨跌幅达到某百分比时提醒。盘中自动监控。"
        "用户说「XX 跌到 1500 提醒我 / 涨过 20 块叫我」时调用。会先让 Jason 确认。"
    ),
    args_model=CreatePriceAlertArgs,
    category="monitor",
    group="watchlist_alerts",
    requires_confirmation=True,
    preview_fn=preview_create_alert,
)
def create_price_alert(symbol: str, alert_type: str, threshold: float,
                       repeat: bool = False, message: Optional[str] = None) -> ToolEnvelope:
    session = get_session()
    try:
        alert_id = f"pa_{uuid.uuid4().hex[:12]}"
        a = PriceAlertRepository(session).add(
            alert_id, symbol, alert_type, float(threshold),
            name=_stock_name(symbol), repeat=1 if repeat else 0, message=message,
        )
        logger.info(f"MoneyBill 创建预警 {alert_id}: {symbol} {alert_type} {threshold}")
        return ToolEnvelope(data={"created": True, **_alert_dict(a)})
    finally:
        session.close()


def preview_delete_alert(args: dict) -> dict:
    """确认前预览：要删除的预警详情。"""
    alert_id = (args.get("alert_id") or "").strip()
    session = get_session()
    try:
        items = PriceAlertRepository(session).list_all(None)
        target = next((a for a in items if a.alert_id == alert_id), None)
    finally:
        session.close()
    if not target:
        return {"error": f"预警 {alert_id} 不存在"}
    return _alert_dict(target)


class DeletePriceAlertArgs(BaseModel):
    alert_id: str = Field(..., min_length=1, description="预警 id，如 pa_xxx")


@tool(
    name="delete_price_alert",
    description="删除一条价格预警（先用 list_price_alerts 拿到 alert_id）。会先让 Jason 确认。",
    args_model=DeletePriceAlertArgs,
    category="monitor",
    group="watchlist_alerts",
    requires_confirmation=True,
    preview_fn=preview_delete_alert,
)
def delete_price_alert(alert_id: str) -> ToolEnvelope:
    session = get_session()
    try:
        ok = PriceAlertRepository(session).delete(alert_id)
        if not ok:
            return ToolEnvelope(business_result="negative", message=f"预警 {alert_id} 不存在")
        return ToolEnvelope(data={"deleted": True, "alert_id": alert_id})
    finally:
        session.close()
