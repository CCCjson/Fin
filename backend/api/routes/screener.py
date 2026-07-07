"""
基本面选股器 API — 多因子筛选全市场（财务 + 估值）

筛选核心已下沉到 screener_engine/service.py，此处仅做 HTTP 封装。
"""
import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from screener_engine.service import (
    OPS as _OPS,
    SCREENER_FIELDS,
    ScreenRequest,
    run_screen as _run_screen,
)
from trading_engine.risk.adapter import get_total_capital, get_max_position_pct

router = APIRouter(prefix="/screener", tags=["基本面选股器"])


@router.get("/fields", summary="可筛字段元数据")
async def get_fields():
    data = [{"field": k, **v} for k, v in SCREENER_FIELDS.items()]
    return {"success": True, "data": data, "ops": sorted(_OPS)}


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
