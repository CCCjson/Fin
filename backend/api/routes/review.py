"""
每日复盘 API
"""
import asyncio
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from loguru import logger

from review.service import ReviewService

router = APIRouter(prefix="/review", tags=["每日复盘"])


# ==================== Pydantic 模型 ====================

class SaveNoteRequest(BaseModel):
    """保存复盘笔记"""
    note: str = Field("", description="Markdown 复盘笔记")
    self_score: Optional[int] = Field(None, ge=1, le=10, description="自评分 1-10")
    template_used: str = Field("beginner", description="模板类型")


# ==================== 路由 ====================

@router.get("/{review_date}", summary="获取某天的完整复盘数据")
async def get_review(
    review_date: str,
    signals_limit: int = Query(20, ge=1, le=200, description="信号每页条数"),
    signals_offset: int = Query(0, ge=0, description="信号偏移量"),
):
    """
    聚合返回指定日期的:
    - 大盘指数表现
    - 持仓当日表现
    - 当日交易记录
    - 当日信号（分页，含是否持有标注）
    - 复盘笔记 + 评分
    - 小白模板
    """
    try:
        d = date.fromisoformat(review_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD")

    service = ReviewService()
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(
        None, service.get_review_data, d, signals_limit, signals_offset
    )
    return data


@router.put("/{review_date}/note", summary="保存复盘笔记和自评分")
async def save_note(review_date: str, req: SaveNoteRequest):
    """保存或更新复盘笔记，可同时设置自评分"""
    try:
        d = date.fromisoformat(review_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误")

    service = ReviewService()
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, service.save_note, d, req.note, req.self_score, req.template_used
        )
        return result
    except Exception as e:
        logger.error(f"保存复盘笔记失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{review_date}/ai-score", summary="请求 AI 评分")
async def request_ai_score(review_date: str):
    """
    调用 LLM 对当天操作进行评分

    评分维度: 纪律执行(30%) + 仓位管理(20%) + 买卖时机(20%) + 信号跟进(15%) + 自我反思(15%)
    """
    try:
        d = date.fromisoformat(review_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误")

    service = ReviewService()
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, service.request_ai_score, d)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"AI评分失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calendar/{year}/{month}", summary="获取月度复盘日历")
async def get_calendar(year: int, month: int):
    """获取指定月份有复盘记录的日期和评分（用于日历渲染）"""
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="月份无效")

    service = ReviewService()
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, service.get_calendar, year, month)
    return {"year": year, "month": month, "reviews": data}


@router.get("/summary/recent", summary="最近复盘摘要")
async def get_summary(limit: int = Query(30, ge=1, le=100)):
    """获取最近 N 天的复盘摘要"""
    service = ReviewService()
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, service.get_summary, limit)
    return {"summaries": data}
