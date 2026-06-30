"""
基本面选股器 API — 多因子筛选全市场（财务 + 估值）
"""
import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, and_

from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo, FinancialData, StockValuation, DailyQuote
from data_engine.storage.repository import ValuationRepository
from trading_engine.risk.adapter import build_broker_info, get_total_capital, get_max_position_pct
from trading_engine.position_sizing import size_position

router = APIRouter(prefix="/screener", tags=["基本面选股器"])


# 可筛字段白名单（field → 来源 + 元数据），防注入
SCREENER_FIELDS = {
    # 财务（来自 financial_data 最新报告期）
    "roe": {"label": "ROE", "source": "financial", "unit": "%"},
    "roa": {"label": "ROA", "source": "financial", "unit": "%"},
    "net_margin": {"label": "净利率", "source": "financial", "unit": "%"},
    "gross_margin": {"label": "毛利率", "source": "financial", "unit": "%"},
    "operating_margin": {"label": "营业利润率", "source": "financial", "unit": "%"},
    "revenue_yoy": {"label": "营收增速", "source": "financial", "unit": "%"},
    "net_profit_yoy": {"label": "净利润增速", "source": "financial", "unit": "%"},
    "debt_ratio": {"label": "资产负债率", "source": "financial", "unit": "%"},
    "current_ratio": {"label": "流动比率", "source": "financial", "unit": ""},
    "eps": {"label": "每股收益", "source": "financial", "unit": "元"},
    "bvps": {"label": "每股净资产", "source": "financial", "unit": "元"},
    # 估值（来自 stock_valuations 最新快照）
    "pe": {"label": "市盈率(静)", "source": "valuation", "unit": ""},
    "pe_ttm": {"label": "市盈率(TTM)", "source": "valuation", "unit": ""},
    "pb": {"label": "市净率", "source": "valuation", "unit": ""},
    "total_mv": {"label": "总市值", "source": "valuation", "unit": "元"},
    "circ_mv": {"label": "流通市值", "source": "valuation", "unit": "元"},
    "dividend_yield": {"label": "股息率", "source": "valuation", "unit": "%"},
}

_OPS = {"gt", "gte", "lt", "lte", "eq", "between"}


def _is_main_board(symbol: str) -> bool:
    """是否沪深主板（5000 本金只能交易主板：排除科创 688/创业 300/北交所）"""
    parts = symbol.split(".")
    code = parts[0]
    suffix = parts[1].upper() if len(parts) > 1 else ""
    if suffix == "BJ":
        return False  # 北交所
    if code.startswith(("688", "689")):
        return False  # 科创板
    if code.startswith(("300", "301")):
        return False  # 创业板
    if code.startswith(("43", "83", "87", "88", "920")):
        return False  # 北交所 / 老三板
    return True


class Filter(BaseModel):
    field: str
    op: str = Field(..., description="gt/gte/lt/lte/eq/between")
    value: float | List[float]


class ScreenRequest(BaseModel):
    filters: List[Filter] = Field(default_factory=list)
    pool_id: Optional[str] = Field(None, description="限定范围: sse50/csi300/csi500/行业名")
    sort_by: Optional[str] = Field(None, description="排序字段（白名单内）")
    sort_desc: bool = True
    limit: int = Field(100, ge=1, le=1000)
    affordable_only: bool = Field(True, description="只保留按当前资金买得起 1 手的股票")
    exclude_st: bool = Field(True, description="排除 ST / *ST / 退市风险股")
    main_board_only: bool = Field(True, description="只看沪深主板（排除科创688/创业300/北交所，小资金无权限）")


@router.get("/fields", summary="可筛字段元数据")
async def get_fields():
    data = [{"field": k, **v} for k, v in SCREENER_FIELDS.items()]
    return {"success": True, "data": data, "ops": sorted(_OPS)}


def _match(val: Optional[float], op: str, target) -> bool:
    if val is None:
        return False  # 缺失因子默认排除
    try:
        if op == "between":
            lo, hi = target
            return lo <= val <= hi
        t = float(target)
        if op == "gt":
            return val > t
        if op == "gte":
            return val >= t
        if op == "lt":
            return val < t
        if op == "lte":
            return val <= t
        if op == "eq":
            return val == t
    except (TypeError, ValueError):
        return False
    return False


def _resolve_universe(pool_id: Optional[str]) -> Optional[set]:
    """返回限定的 symbol 集合；None 表示全市场"""
    if not pool_id:
        return None
    from api.routes.stock_pools import INDEX_POOLS, _fetch_index_constituents, _fetch_industry_stocks
    if pool_id in INDEX_POOLS:
        stocks = _fetch_index_constituents(INDEX_POOLS[pool_id]["index_code"])
    else:
        stocks = _fetch_industry_stocks(pool_id)  # 按行业名
    return {s["symbol"] for s in stocks}


def _run_screen(req: ScreenRequest) -> List[dict]:
    """同步执行筛选（在线程池中调用）"""
    session = get_session()
    try:
        # 最新财务（每 symbol 取 max(report_date)）
        sub = session.query(
            FinancialData.symbol,
            func.max(FinancialData.report_date).label("md"),
        ).group_by(FinancialData.symbol).subquery()
        fin_rows = session.query(FinancialData).join(
            sub, and_(FinancialData.symbol == sub.c.symbol,
                      FinancialData.report_date == sub.c.md)
        ).all()
        fin_map = {f.symbol: f for f in fin_rows}

        # 最新估值
        val_map = ValuationRepository(session).get_all_latest()

        # 最新收盘价（每 symbol 取 max(date)）—— 估值快照无股价，需单独取以算可负担性
        psub = session.query(
            DailyQuote.symbol,
            func.max(DailyQuote.date).label("md"),
        ).group_by(DailyQuote.symbol).subquery()
        price_rows = session.query(DailyQuote.symbol, DailyQuote.close).join(
            psub, and_(DailyQuote.symbol == psub.c.symbol,
                       DailyQuote.date == psub.c.md)
        ).all()
        price_map = {sym: close for sym, close in price_rows}

        # 名称
        name_map = {s.symbol: s.name for s in session.query(StockInfo).all()}

        # 资金量：按真实总资金 + 可用现金 + 集中度上限算建议买入（构建一次复用）
        total_capital = get_total_capital()
        max_pct = get_max_position_pct()
        target_pct = max_pct * 100.0
        broker_info = build_broker_info(total_capital)

        universe = _resolve_universe(req.pool_id)
        symbols = set(fin_map) | set(val_map)
        if universe is not None:
            symbols &= universe

        results = []
        for sym in symbols:
            # 只看沪深主板（小资金无科创/创业/北交所权限）
            if req.main_board_only and not _is_main_board(sym):
                continue
            # 排除 ST / *ST / 退市风险股（按名称）
            if req.exclude_st:
                nm = name_map.get(sym) or ""
                if "ST" in nm.upper() or "退" in nm:
                    continue

            fin = fin_map.get(sym)
            val = val_map.get(sym)

            def _get(field: str):
                src = SCREENER_FIELDS[field]["source"]
                obj = fin if src == "financial" else val
                return getattr(obj, field, None) if obj else None

            # 应用所有过滤条件（AND）
            ok = True
            for f in req.filters:
                if f.field not in SCREENER_FIELDS:
                    continue  # 非白名单字段忽略
                if not _match(_get(f.field), f.op, f.value):
                    ok = False
                    break
            if not ok:
                continue

            # 按真实资金算建议买入（以集中度上限为目标，硬约束现金+风控）
            price = price_map.get(sym)
            sizing = size_position(
                sym, price, target_pct,
                broker_info=broker_info, total_capital=total_capital, max_position_pct=max_pct,
            )
            # 只推买得起 1 手的票（自动滤掉股价过高 / 现金不足，如创业板贵股）
            if req.affordable_only and not sizing.get("affordable"):
                continue

            row = {"symbol": sym, "name": name_map.get(sym), "price": price}
            for field in SCREENER_FIELDS:
                row[field] = _get(field)
            row["suggested"] = {
                "shares": sizing["shares"],
                "lots": sizing["lots"],
                "amount": sizing["amount"],
                "affordable": sizing["affordable"],
                "risk_passed": sizing["risk_passed"],
            }
            results.append(row)

        # 排序
        if req.sort_by in SCREENER_FIELDS:
            results.sort(
                key=lambda r: (r.get(req.sort_by) is None, r.get(req.sort_by) or 0),
                reverse=req.sort_desc,
            )
        return results[: req.limit]
    finally:
        session.close()


@router.post("/run", summary="运行多因子筛选")
async def run_screen(req: ScreenRequest):
    # 校验 op
    for f in req.filters:
        if f.op not in _OPS:
            return JSONResponse(status_code=400, content={"success": False, "error": f"非法 op: {f.op}"})
        if f.field not in SCREENER_FIELDS:
            return JSONResponse(status_code=400, content={"success": False, "error": f"非法字段: {f.field}"})
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, _run_screen, req)
        total_capital = await loop.run_in_executor(None, get_total_capital)
        max_position_pct = await loop.run_in_executor(None, get_max_position_pct)
        return {
            "success": True,
            "count": len(results),
            "total_capital": total_capital,
            "max_position_pct": max_position_pct,
            "affordable_only": req.affordable_only,
            "data": results,
        }
    except Exception as e:
        logger.error(f"选股器运行失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/refresh-valuation", summary="刷新全市场估值快照（流式）")
async def refresh_valuation():
    async def _streaming():
        loop = asyncio.get_event_loop()
        try:
            yield json.dumps({"event": "start", "message": "开始刷新全市场估值..."}, ensure_ascii=False) + "\n"
            from scripts.backfill_valuation import refresh_all_valuations
            result = await loop.run_in_executor(None, refresh_all_valuations)
            yield json.dumps({"event": "done", "result": result}, ensure_ascii=False) + "\n"
        except Exception as e:
            logger.error(f"估值刷新失败: {e}")
            yield json.dumps({"event": "error", "message": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
