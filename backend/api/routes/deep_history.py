"""
深历史日线数据回补 API —— A股往前补到 1990、港美股全量拉取（1990/1970 起）。

两个常驻后台任务（AShareDeepHistoryJob / OverseasDeepHistoryJob），起停走 POST，
进度靠轮询 GET，跟 knowledge.py 里 cninfo 批量摄入同一套模式。
"""
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/deep-history", tags=["深历史回补"])


# ==================== 请求模型 ====================

class AShareStartRequest(BaseModel):
    workers: Optional[int] = Field(None, ge=1, le=6, description="并发 worker 数，默认 4")
    symbols: Optional[List[str]] = Field(None, description="仅测试用：显式指定股票代码列表")
    limit: Optional[int] = Field(None, ge=1, description="仅测试用：只处理候选队列里的前 N 只")


class OverseasStartRequest(BaseModel):
    market: str = Field(..., description="hk_stock 或 us_stock")
    symbols: Optional[List[str]] = Field(None, description="仅测试用：显式指定股票代码列表")
    limit: Optional[int] = Field(None, ge=1, description="仅测试用：只处理待办队列里的前 N 只")
    batch_size: int = Field(50, ge=1, le=200, description="yf.download 每批股票数")
    sleep_between_batches: float = Field(3.0, ge=0, description="批次间隔秒数")
    max_retry: int = Field(3, ge=1, le=10, description="单批最大重试次数")


# ==================== A股深历史 ====================

@router.post("/a-share/start", summary="启动 A股 深历史回补（后台任务）")
async def a_share_start(req: AShareStartRequest):
    from data_engine.deep_history.a_share_job import a_share_deep_history_job
    return a_share_deep_history_job.start(workers=req.workers, symbols=req.symbols, limit=req.limit)


@router.get("/a-share/status", summary="查询 A股 深历史回补进度")
async def a_share_status():
    from data_engine.deep_history.a_share_job import a_share_deep_history_job
    return a_share_deep_history_job.snapshot()


@router.post("/a-share/stop", summary="停止 A股 深历史回补（处理完在途请求后停）")
async def a_share_stop():
    from data_engine.deep_history.a_share_job import a_share_deep_history_job
    return a_share_deep_history_job.stop()


# ==================== 港股/美股深历史（共用一个任务，market 参数区分） ====================

@router.post("/overseas/start", summary="启动港股/美股深历史回补（后台任务）")
async def overseas_start(req: OverseasStartRequest):
    from data_engine.deep_history.overseas_job import overseas_deep_history_job
    return overseas_deep_history_job.start(
        market=req.market, symbols=req.symbols, limit=req.limit,
        batch_size=req.batch_size, sleep_between_batches=req.sleep_between_batches,
        max_retry=req.max_retry,
    )


@router.get("/overseas/status", summary="查询港股/美股深历史回补进度")
async def overseas_status():
    from data_engine.deep_history.overseas_job import overseas_deep_history_job
    return overseas_deep_history_job.snapshot()


@router.post("/overseas/stop", summary="停止港股/美股深历史回补（处理完当前批次后停）")
async def overseas_stop():
    from data_engine.deep_history.overseas_job import overseas_deep_history_job
    return overseas_deep_history_job.stop()
