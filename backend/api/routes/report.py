"""
AI分析报告API
"""
import asyncio
import json
import uuid
from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import AnalysisReport
from report_engine.data_collector import ReportDataCollector
from report_engine.prompt_builder import ReportPromptBuilder
from report_engine.generator import ReportGenerator

router = APIRouter(prefix="/reports", tags=["AI分析报告"])


class GenerateReportRequest(BaseModel):
    """生成报告请求"""
    report_type: str = Field("weekly", description="报告类型: weekly / monthly")
    model: str = Field("gpt-4o", description="模型: gpt-4o / gpt-4o-mini")
    period_end: Optional[str] = Field(None, description="报告截止日期 (YYYY-MM-DD)，默认今天")


@router.post("/generate", summary="流式生成AI分析报告")
async def generate_report(request: GenerateReportRequest):
    """
    流式生成AI深度投资分析报告（NDJSON）

    事件类型:
    - collecting: 正在收集数据
    - start: 开始生成
    - chunk: Markdown内容片段
    - done: 生成完成
    - error: 生成失败
    """
    try:
        # 解析日期
        period_end = None
        if request.period_end:
            period_end = date.fromisoformat(request.period_end)

        loop = asyncio.get_event_loop()

        # 1. 收集数据（在线程池中执行，避免阻塞事件循环）
        def _collect():
            collector = ReportDataCollector()
            return collector.collect(report_type=request.report_type, period_end=period_end)

        # 先发送 collecting 事件，然后开始异步数据采集
        async def _streaming():
            # 发送 collecting 事件
            yield json.dumps({"event": "collecting", "message": "正在收集市场数据..."}, ensure_ascii=False) + "\n"
            await asyncio.sleep(0)

            # 在线程池中执行数据采集
            data = await loop.run_in_executor(None, _collect)

            yield json.dumps({"event": "collecting", "message": "正在构建分析提示..."}, ensure_ascii=False) + "\n"
            await asyncio.sleep(0)

            # 2. 构建Prompt
            builder = ReportPromptBuilder()
            system_prompt, user_prompt = builder.build(data, request.report_type)

            # 3. 生成
            report_id = f"rpt_{uuid.uuid4().hex[:12]}"
            period_label = "周报" if request.report_type == "weekly" else "月报"
            title = f"量化投资{period_label} ({data['period_start']} ~ {data['period_end']})"

            generator = ReportGenerator()
            sync_gen = generator.generate_stream(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                report_id=report_id,
                title=title,
                model=request.model,
                report_type=request.report_type,
                period_start=data["period_start"],
                period_end=data["period_end"],
                data_snapshot=json.dumps(data, ensure_ascii=False, default=str),
            )

            for chunk in sync_gen:
                yield chunk
                await asyncio.sleep(0)

        return StreamingResponse(
            _streaming(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as e:
        logger.error(f"生成报告失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", summary="获取报告列表")
async def list_reports(
    report_type: Optional[str] = Query(None, description="报告类型筛选"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取报告列表（不含content）"""
    session = get_session()
    try:
        query = session.query(AnalysisReport)
        if report_type:
            query = query.filter(AnalysisReport.report_type == report_type)

        total = query.count()
        reports = query.order_by(AnalysisReport.created_at.desc()).offset(offset).limit(limit).all()

        items = []
        for r in reports:
            items.append({
                "report_id": r.report_id,
                "report_type": r.report_type,
                "title": r.title,
                "status": r.status,
                "model_used": r.model_used,
                "token_count": r.token_count,
                "generation_time_seconds": r.generation_time_seconds,
                "period_start": str(r.period_start) if r.period_start else None,
                "period_end": str(r.period_end) if r.period_end else None,
                "created_at": str(r.created_at) if r.created_at else None,
            })

        return {"total": total, "reports": items}
    finally:
        session.close()


@router.get("/{report_id}", summary="获取单个报告详情")
async def get_report(report_id: str):
    """获取报告详情（含完整content）"""
    session = get_session()
    try:
        report = session.query(AnalysisReport).filter(
            AnalysisReport.report_id == report_id
        ).first()

        if not report:
            raise HTTPException(status_code=404, detail="报告不存在")

        return {
            "report_id": report.report_id,
            "report_type": report.report_type,
            "title": report.title,
            "content": report.content,
            "status": report.status,
            "model_used": report.model_used,
            "token_count": report.token_count,
            "generation_time_seconds": report.generation_time_seconds,
            "period_start": str(report.period_start) if report.period_start else None,
            "period_end": str(report.period_end) if report.period_end else None,
            "error_message": report.error_message,
            "created_at": str(report.created_at) if report.created_at else None,
        }
    finally:
        session.close()


@router.delete("/{report_id}", summary="删除报告")
async def delete_report(report_id: str):
    """删除指定报告"""
    session = get_session()
    try:
        report = session.query(AnalysisReport).filter(
            AnalysisReport.report_id == report_id
        ).first()

        if not report:
            raise HTTPException(status_code=404, detail="报告不存在")

        session.delete(report)
        session.commit()
        return {"message": "报告已删除", "report_id": report_id}
    finally:
        session.close()
