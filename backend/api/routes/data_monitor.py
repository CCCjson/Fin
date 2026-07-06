"""
数据更新监控 API — 一站式聚合前端监控面板所需的全部状态。

前缀 /data-monitor（避开已存在的 monitor.router）。手动触发更新沿用现成的
POST /data/update-daily/stream，本模块不重复实现。
"""
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger
from sqlalchemy import func

from data_engine.health import get_coverage, get_freshness
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    DataUpdateLog, FinancialData, LimitUpPool, NewsArticle,
    RealtimeSnapshot, StockInfo, StockValuation,
)

router = APIRouter(prefix="/data-monitor", tags=["数据监控"])


def _asset_realtime(session) -> dict:
    """实时快照资产状态：最新快照时间 + 该快照条数。"""
    latest = session.query(func.max(RealtimeSnapshot.snapshot_time)).scalar()
    count = 0
    if latest:
        count = session.query(func.count(RealtimeSnapshot.id)).filter(
            RealtimeSnapshot.snapshot_time == latest
        ).scalar() or 0
    return {
        "latest_snapshot": latest.isoformat() if latest else None,
        "count_at_latest": int(count),
        "is_today": bool(latest and latest.date() == date.today()),
    }


def _asset_financial(session) -> dict:
    """财务数据资产状态：覆盖度 + 最新报告期 + 近期有报占比 + 上次回补。"""
    total_stocks = session.query(func.count(StockInfo.symbol)).filter(
        StockInfo.stock_type == "stock",
        StockInfo.is_active == 1,
    ).scalar() or 0

    symbols_with_data = session.query(
        func.count(func.distinct(FinancialData.symbol))
    ).scalar() or 0

    latest_report = session.query(func.max(FinancialData.report_date)).scalar()

    recent_cutoff = date.today() - timedelta(days=180)
    symbols_recent = session.query(
        func.count(func.distinct(FinancialData.symbol))
    ).filter(FinancialData.report_date >= recent_cutoff).scalar() or 0

    last_log = session.query(DataUpdateLog).filter(
        DataUpdateLog.update_type == "financial"
    ).order_by(DataUpdateLog.completed_at.desc()).first()

    return {
        "total_stocks": int(total_stocks),
        "symbols_with_data": int(symbols_with_data),
        "coverage_pct": round(symbols_with_data / total_stocks * 100, 1) if total_stocks else 0,
        "latest_report_date": latest_report.isoformat() if latest_report else None,
        "symbols_recent_report": int(symbols_recent),
        "last_backfill": {
            "status": last_log.status,
            "completed_at": last_log.completed_at.isoformat() if last_log.completed_at else None,
        } if last_log else None,
    }


def _asset_valuation(session) -> dict:
    """估值快照资产状态：最新快照日 + 该日条数 + 距今天数。"""
    latest = session.query(func.max(StockValuation.snapshot_date)).scalar()
    count = 0
    days_old = None
    if latest:
        count = session.query(func.count(StockValuation.id)).filter(
            StockValuation.snapshot_date == latest
        ).scalar() or 0
        latest_d = latest.date() if isinstance(latest, datetime) else latest
        days_old = (date.today() - latest_d).days
    return {
        "latest_date": latest.isoformat() if latest else None,
        "count_at_latest": int(count),
        "days_old": days_old,
    }


def _asset_news(session) -> dict:
    """新闻资产状态：总量 + 近 7 天量 + 最新发布时间。"""
    total = session.query(func.count(NewsArticle.id)).scalar() or 0
    week_ago = datetime.now() - timedelta(days=7)
    last_7d = session.query(func.count(NewsArticle.id)).filter(
        NewsArticle.published_at >= week_ago
    ).scalar() or 0
    latest = session.query(func.max(NewsArticle.published_at)).scalar()
    return {
        "total": int(total),
        "last_7d": int(last_7d),
        "latest_published_at": latest.isoformat() if latest else None,
    }


def _asset_limit_up(session) -> dict:
    """涨停池资产状态：最新一天的家数/连板梯队摘要/炸板率。

    盘中如果当天数据还没落库，前端仍会走 get_pool_overview 同款的盘中缓存兜底
    （这里只做"资产健康"展示，用 DB 最新一天即可，不必重复接盘中缓存逻辑）。
    """
    latest_date = session.query(func.max(LimitUpPool.trade_date)).scalar()
    if not latest_date:
        return {"latest_date": None, "count_today": 0, "ladder_summary": "-", "break_rate": None}

    zt_count = session.query(func.count(LimitUpPool.id)).filter(
        LimitUpPool.trade_date == latest_date, LimitUpPool.pool_type == "zt",
    ).scalar() or 0
    zb_count = session.query(func.count(LimitUpPool.id)).filter(
        LimitUpPool.trade_date == latest_date, LimitUpPool.pool_type == "zb",
    ).scalar() or 0
    total = zt_count + zb_count
    break_rate = round(zb_count / total * 100, 1) if total else None

    ladder_rows = session.query(LimitUpPool.consecutive_boards).filter(
        LimitUpPool.trade_date == latest_date, LimitUpPool.pool_type == "zt",
    ).all()
    ladder: dict = {}
    for (boards,) in ladder_rows:
        if boards is None:
            continue
        key = "7+" if boards >= 7 else str(boards)
        ladder[key] = ladder.get(key, 0) + 1
    ladder_summary = "/".join(f"{k}板{v}只" for k, v in sorted(ladder.items(), key=lambda kv: kv[0])) or "-"

    return {
        "latest_date": latest_date.isoformat(),
        "count_today": int(zt_count),
        "break_count": int(zb_count),
        "ladder_summary": ladder_summary,
        "break_rate": break_rate,
        "is_today": latest_date == date.today(),
    }


def _recent_update_logs(session, limit: int = 10) -> list:
    logs = (
        session.query(DataUpdateLog)
        .order_by(DataUpdateLog.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": lg.id,
            "market": lg.market,
            "update_type": lg.update_type,
            "symbols_count": lg.symbols_count,
            "records_count": lg.records_count,
            "status": lg.status,
            "error_message": lg.error_message,
            "started_at": lg.started_at.isoformat() if lg.started_at else None,
            "completed_at": lg.completed_at.isoformat() if lg.completed_at else None,
            "duration_seconds": lg.duration_seconds,
        }
        for lg in logs
    ]


def _catch_up(session, scheduler_status: dict) -> dict:
    """判断「今天该更却没更」——用于前端弹「一键补跑」提示。

    判据（全部满足才 needed）：
      1) 自动更新是开启的（关了就不唠叨）
      2) 今天是工作日（交易日近似；节假日可能误报一次，点补跑也拉不到新数据，无害）
      3) 当前时间已过今天的计划触发点（scheduled_hour:scheduled_minute）
      4) 今天还没有一条日线更新记录（DataUpdateLog，落库、不受重启影响，
         且绕开 coverage_pct 被个别股票顶偏的失真问题）
    """
    today = date.today()
    is_weekday = today.weekday() < 5

    # 今天是否已有日线更新记录（手动「立即更新」也会写 → 补跑后自动消失）
    today_start = datetime.combine(today, time.min)
    updated_today = session.query(DataUpdateLog.id).filter(
        DataUpdateLog.update_type.in_(["daily", "daily_incremental"]),
        DataUpdateLog.started_at >= today_start,
    ).first() is not None

    # 上次日线更新的日期（提示里展示「数据还停在哪天」）
    last_log = session.query(DataUpdateLog).filter(
        DataUpdateLog.update_type.in_(["daily", "daily_incremental"])
    ).order_by(DataUpdateLog.started_at.desc()).first()
    last_daily_update = (
        last_log.started_at.date().isoformat()
        if last_log and last_log.started_at else None
    )

    sh = scheduler_status.get("scheduled_hour", 15)
    sm = scheduler_status.get("scheduled_minute", 35)
    scheduled_time = f"{sh:02d}:{sm:02d}"
    past_scheduled = datetime.now() >= datetime.combine(today, time(sh, sm))

    needed = bool(
        scheduler_status.get("enabled")
        and is_weekday
        and past_scheduled
        and not updated_today
    )

    return {
        "needed": needed,
        "reason": (
            f"今日交易日已过 {scheduled_time}，但尚未检测到日线更新"
            if needed else ""
        ),
        "scheduled_time": scheduled_time,
        "last_daily_update": last_daily_update,
    }


@router.get("/overview", summary="数据更新监控总览（一次性聚合）")
async def get_overview():
    """前端轮询这一个接口即可拿到面板全部数据。"""
    session = get_session()
    try:
        from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
        scheduler_status = daily_pipeline_scheduler.get_status()

        return {
            "coverage": get_coverage(),
            "freshness": get_freshness(session),
            "assets": {
                "realtime": _asset_realtime(session),
                "financial": _asset_financial(session),
                "valuation": _asset_valuation(session),
                "news": _asset_news(session),
                "limit_up": _asset_limit_up(session),
            },
            "recent_update_logs": _recent_update_logs(session),
            "scheduler": scheduler_status,
            "catch_up": _catch_up(session, scheduler_status),
            "server_time": datetime.now().isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"数据监控总览查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/events", summary="最近业务事件（活动流种子/轮询）")
async def get_events(limit: int = 30):
    """返回业务事件总线里的滚动日志（最新在前），供前端活动流首屏 seed。"""
    try:
        from business_events import BUS
        return {"events": BUS.recent(limit)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取业务事件失败: {e}")
        return {"events": []}


class SchedulerToggleRequest(BaseModel):
    enabled: bool


@router.post("/scheduler/toggle", summary="开/关每日自动更新（运行时，无需改 .env）")
async def toggle_scheduler(request: SchedulerToggleRequest):
    """运行时动态开关自动更新定时任务。"""
    try:
        from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
        return daily_pipeline_scheduler.set_enabled(request.enabled)
    except Exception as e:  # noqa: BLE001
        logger.error(f"切换自动更新开关失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/limit-up/detail", summary="涨停池详情（含完整股票清单，供卡片展开查看）")
async def get_limit_up_detail(trade_date: Optional[str] = None):
    """数据监控页「涨停池」卡片展开详情用：完整连板榜+炸板榜（不是聚合数字）。"""
    try:
        from limit_up_engine.service import get_pool_overview
        return get_pool_overview(trade_date, include_zhaban=True)
    except Exception as e:  # noqa: BLE001
        logger.error(f"涨停池详情查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/limit-up/refresh", summary="手动刷新涨停池数据（同步阻塞，走线程池）")
async def refresh_limit_up():
    """手动触发涨停池抓取 + 候选打分（同 daily_pipeline 里的步骤，供数据监控页"刷新"按钮用）。"""
    import asyncio
    try:
        from limit_up_engine.service import run_daily_prediction
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_daily_prediction)
        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"涨停池手动刷新失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
