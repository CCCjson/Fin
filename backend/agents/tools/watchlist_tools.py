"""
自选股类工具 —— 包 WatchlistRepository 的自选股管理。

加/删走二次确认；预警相关见 alert_tools.py。
"""
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo
from data_engine.storage.repository import WatchlistRepository


def _find_by_symbol(session, symbol: str):
    items = WatchlistRepository(session).list_all()
    return [w for w in items if w.symbol == symbol]


class GetWatchlistArgs(BaseModel):
    pass


@tool(
    name="get_watchlist",
    description="查看自选股列表（含分组、备注）。用户问「我的自选股有哪些」时调用。",
    args_model=GetWatchlistArgs,
    category="data",
    group="watchlist_alerts",
)
def get_watchlist() -> ToolEnvelope:
    session = get_session()
    try:
        items = WatchlistRepository(session).list_all()
        data = [{"symbol": w.symbol, "name": w.name,
                 "group_name": w.group_name, "note": w.note} for w in items]
    finally:
        session.close()
    if not data:
        return ToolEnvelope(business_result="negative", message="自选股列表是空的。")
    groups: dict[str, list] = {}
    for d in data:
        groups.setdefault(d["group_name"] or "默认分组", []).append(d)
    return ToolEnvelope(data={"count": len(data), "groups": groups})


def preview_add_watchlist(args: dict) -> dict:
    symbol = (args.get("symbol") or "").strip()
    if not symbol:
        return {"error": "缺少股票代码"}
    session = get_session()
    try:
        info = session.query(StockInfo).filter(StockInfo.symbol == symbol).first()
        existing = _find_by_symbol(session, symbol)
    finally:
        session.close()
    return {
        "symbol": symbol,
        "name": info.name if info else None,
        "group_name": args.get("group_name") or "默认分组",
        "note": args.get("note"),
        "already_in": bool(existing),
    }


class AddToWatchlistArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    group_name: str = Field("默认分组", description="分组名，默认「默认分组」")
    note: Optional[str] = Field(None, description="可选备注")


@tool(
    name="add_to_watchlist",
    description="把股票加入自选股（可指定分组、备注）。用户说「把XX加自选/关注一下XX」时调用。会先让 Jason 确认。",
    args_model=AddToWatchlistArgs,
    category="data",
    group="watchlist_alerts",
    requires_confirmation=True,
    preview_fn=preview_add_watchlist,
)
def add_to_watchlist(symbol: str, group_name: str = "默认分组",
                     note: Optional[str] = None) -> ToolEnvelope:
    session = get_session()
    try:
        if _find_by_symbol(session, symbol):
            return ToolEnvelope(business_result="negative", message=f"{symbol} 已在自选股里，无需重复添加。")
        info = session.query(StockInfo).filter(StockInfo.symbol == symbol).first()
        name = info.name if info else None
        w = WatchlistRepository(session).add(symbol, name, group_name, note)
        logger.info(f"MoneyBill 添加自选股: {symbol} → {group_name}")
        return ToolEnvelope(data={"added": True, "symbol": w.symbol, "name": w.name,
                                  "group_name": w.group_name})
    finally:
        session.close()


def preview_remove_watchlist(args: dict) -> dict:
    symbol = (args.get("symbol") or "").strip()
    session = get_session()
    try:
        matched = _find_by_symbol(session, symbol)
    finally:
        session.close()
    if not matched:
        return {"error": f"{symbol} 不在自选股里"}
    return {"symbol": symbol,
            "items": [{"name": w.name, "group_name": w.group_name} for w in matched]}


class RemoveFromWatchlistArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")


@tool(
    name="remove_from_watchlist",
    description="把股票从自选股移除。用户说「把XX从自选删掉/取消关注」时调用。会先让 Jason 确认。",
    args_model=RemoveFromWatchlistArgs,
    category="data",
    group="watchlist_alerts",
    requires_confirmation=True,
    preview_fn=preview_remove_watchlist,
)
def remove_from_watchlist(symbol: str) -> ToolEnvelope:
    session = get_session()
    try:
        matched = _find_by_symbol(session, symbol)
        if not matched:
            return ToolEnvelope(business_result="negative", message=f"{symbol} 不在自选股里。")
        repo = WatchlistRepository(session)
        for w in matched:
            repo.delete(w.id)
        return ToolEnvelope(data={"removed": True, "symbol": symbol, "count": len(matched)})
    finally:
        session.close()
