"""
信号追踪 API — 追踪信号实际表现、按策略聚合分析
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import date
from loguru import logger

from analysis_engine.signal_tracker import SignalTracker

router = APIRouter(prefix="/tracking", tags=["信号追踪"])


@router.post("/update")
async def update_tracking():
    """
    触发批量更新：为所有信号计算/更新追踪数据
    """
    try:
        tracker = SignalTracker()
        result = tracker.update_all()
        tracker.close()
        return result
    except Exception as e:
        logger.error(f"追踪更新失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_stats(
    strategy: Optional[str] = None,
    days: Optional[int] = Query(default=None, ge=1, le=3650),
):
    """
    获取追踪统计（总体 + 按策略）

    Args:
        strategy: 筛选策略名称
        days: 只统计最近 N 天的信号
    """
    try:
        tracker = SignalTracker()
        stats = tracker.get_strategy_stats(strategy=strategy, days=days)
        tracker.close()
        return stats
    except Exception as e:
        logger.error(f"获取追踪统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals")
async def get_tracked_signals(
    strategy: Optional[str] = None,
    outcome: Optional[str] = None,
    signal_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
):
    """
    查询追踪信号列表（分页 + 筛选）

    Args:
        strategy: 策略名称
        outcome: 结果 (win/loss/neutral)
        signal_type: 信号类型 (BUY/SELL)
        start_date: 开始日期
        end_date: 结束日期
        limit: 每页条数
        offset: 偏移量
    """
    try:
        tracker = SignalTracker()
        result = tracker.get_tracked_signals(
            strategy=strategy,
            outcome=outcome,
            signal_type=signal_type,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        tracker.close()
        return result
    except Exception as e:
        logger.error(f"查询追踪信号失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/{signal_id}")
async def get_signal_detail(signal_id: str):
    """
    获取单个信号的追踪详情

    Args:
        signal_id: 信号唯一标识
    """
    try:
        tracker = SignalTracker()
        detail = tracker.get_signal_detail(signal_id)
        tracker.close()

        if not detail:
            raise HTTPException(status_code=404, detail=f"追踪记录不存在: {signal_id}")

        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取信号详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
