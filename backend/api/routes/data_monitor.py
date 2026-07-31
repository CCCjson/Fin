"""
数据更新监控 API — 一站式聚合前端监控面板所需的全部状态。

前缀 /data-monitor（避开已存在的 monitor.router）。手动触发更新沿用现成的
POST /data/update-daily/stream，本模块不重复实现。
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from common.market import A_SHARE
from common.market_time import market_day_bounds, market_now, market_today, utc_iso, utc_now
from sqlalchemy import func

from data_engine.health import get_coverage, get_freshness
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    DataUpdateLog, FinancialData, LimitUpPool, NewsArticle,
    RealtimeSnapshot, StockInfo, StockValuation,
)

router = APIRouter(prefix="/data-monitor", tags=["数据监控"])


def _asset_realtime(session) -> dict:
    """实时快照资产状态：最新快照时间 + 该快照条数。

    ⚠️ **条数必须用子查询在 SQL 里比，不能把 max() 取回 Python 再绑回去过滤。**
    `snapshot_time` 是 DateTime，SQLite 存 `'2026-07-07 05:07:37'`；取回来是
    `datetime(...)`，再绑回去 SQLAlchemy 渲染成 `'2026-07-07 05:07:37.000000'`
    —— 带微秒，字符串比不上，**count 恒为 0**。
    实测：库里明明有 6594 行，这张卡一直显示 0 只。（2026-07-27 修）
    """
    latest = session.query(func.max(RealtimeSnapshot.snapshot_time)).scalar()
    count = 0
    if latest:
        newest = session.query(func.max(RealtimeSnapshot.snapshot_time)).scalar_subquery()
        count = session.query(func.count(RealtimeSnapshot.id)).filter(
            RealtimeSnapshot.snapshot_time == newest
        ).scalar() or 0
    return {
        "latest_snapshot": utc_iso(latest),
        "count_at_latest": int(count),
        "is_today": bool(latest and latest.date() == market_today(A_SHARE)),
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

    recent_cutoff = market_today(A_SHARE) - timedelta(days=180)
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
            "completed_at": utc_iso(last_log.completed_at),
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
        days_old = (market_today(A_SHARE) - latest_d).days
    return {
        "latest_date": latest.isoformat() if latest else None,
        "count_at_latest": int(count),
        "days_old": days_old,
    }


def _asset_news(session) -> dict:
    """新闻资产状态：总量 + 近 7 天量 + 最新发布时间。

    ⚠️ cutoff 必须用 `utc_now()`。`NewsArticle.published_at` 存的是 **naive UTC**
    （`news_engine/fetcher.py` 写的是 `utc_now()`），拿本地 UTC+8 的
    `datetime.now()` 当 cutoff 会让窗口右移 8 小时 → **近 7 天条数系统性少算**。
    时区统一工程（`common/market_time`）的漏网之鱼，2026-07-27 补上。
    """
    total = session.query(func.count(NewsArticle.id)).scalar() or 0
    week_ago = utc_now() - timedelta(days=7)
    last_7d = session.query(func.count(NewsArticle.id)).filter(
        NewsArticle.published_at >= week_ago
    ).scalar() or 0
    latest = session.query(func.max(NewsArticle.published_at)).scalar()
    return {
        "total": int(total),
        "last_7d": int(last_7d),
        "latest_published_at": utc_iso(latest),
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
        "is_today": latest_date == market_today(A_SHARE),
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
            "started_at": utc_iso(lg.started_at),
            "completed_at": utc_iso(lg.completed_at),
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
    # 这个面板是 A 股视角（涨停/估值/财报/日线更新全是 A 股口径）
    today = market_today(A_SHARE)
    is_weekday = today.weekday() < 5

    # 今天是否已有日线更新记录（手动「立即更新」也会写 → 补跑后自动消失）
    #
    # `DataUpdateLog.started_at` 现已随写入侧翻成 naive UTC（scripts/migrate_to_utc 迁了历史，
    # daily_updater/financial_updater 写入侧改成 utc_now），边界走默认 utc 口径即可。
    today_start, today_end = market_day_bounds(A_SHARE, today)
    updated_today = session.query(DataUpdateLog.id).filter(
        DataUpdateLog.update_type.in_(["daily", "daily_incremental"]),
        DataUpdateLog.started_at >= today_start,
        DataUpdateLog.started_at < today_end,
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
    # 「现在过没过 15:35」按 A 股墙钟判（两边同为该市场时区，不跨口径）
    now_sh = market_now(A_SHARE)
    past_scheduled = (now_sh.hour, now_sh.minute) >= (sh, sm)

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
    def _work():
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
                "server_time": utc_iso(utc_now()),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"数据监控总览查询失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            session.close()
    return await asyncio.to_thread(_work)


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


# ======================================================================
# 资产注册表 / 运行 / 缺口 / 调度器 —— 数据积累重构新增（doc16）
#
# 老端点（/data/update-daily/stream 等）**保留不删**，别处还在用；新面板走这里。
# ======================================================================


@router.get("/assets", summary="数据资产矩阵（注册表快照 + 各自当前状态）")
async def get_assets():
    """前端「资产矩阵」的数据源：有哪些资产、各自更新到哪天、健不健康。"""
    def _work():
        from data_engine.asset_status import asset_matrix
        session = get_session()
        try:
            return asset_matrix(session)
        finally:
            session.close()
    try:
        return await asyncio.to_thread(_work)
    except Exception as e:  # noqa: BLE001
        logger.error(f"资产矩阵查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class RunRequest(BaseModel):
    # 资产 key 列表；省略 = 注册表里所有 in_update_all=True 的
    scope: Optional[list[str]] = None
    mode: str = "incremental"      # incremental | gap_fill


@router.post("/runs", summary="发起一次数据更新（后台任务，关页面不断）")
async def start_run(request: RunRequest):
    """「一键更新全部」的入口。立即返回，进度轮询 `/runs/current`。"""
    from data_engine.update_all_job import update_all_job
    if request.mode not in ("incremental", "gap_fill"):
        raise HTTPException(status_code=400, detail=f"未知 mode: {request.mode}")
    return update_all_job.start(scope=request.scope, mode=request.mode)


@router.get("/runs/current", summary="当前/最近一次更新的进度快照")
async def get_current_run():
    from data_engine.update_all_job import update_all_job
    return update_all_job.snapshot()


@router.post("/runs/stop", summary="停止当前更新（协作式，跑完当前资产再停）")
async def stop_run():
    from data_engine.update_all_job import update_all_job
    return update_all_job.stop()


@router.get("/gaps", summary="数据缺口列表（哪几天本该有数据但没有）")
async def get_gaps(status: Optional[str] = None, market: Optional[str] = None):
    def _work():
        from data_engine.gap_engine import gap_summary, list_gaps
        session = get_session()
        try:
            return {
                "gaps": list_gaps(session, status=status, market=market),
                "summary": gap_summary(session),
            }
        finally:
            session.close()
    try:
        return await asyncio.to_thread(_work)
    except Exception as e:  # noqa: BLE001
        logger.error(f"缺口查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class GapFillRequest(BaseModel):
    scan_only: bool = False
    asset_key: Optional[str] = None
    lookback_days: Optional[int] = None


@router.post("/gaps/scan", summary="扫描 + 自动补齐数据缺口（后台任务）")
async def scan_and_fill_gaps(request: GapFillRequest):
    """⛔ 只会自动补 `certain`（基准指数自证）的缺口；`suspected` 的只扫不补。"""
    from data_engine.gap_job import gap_fill_job
    return gap_fill_job.start(
        scan_only=request.scan_only,
        asset_key=request.asset_key,
        lookback_days=request.lookback_days,
    )


@router.get("/gaps/job", summary="缺口补齐任务的进度快照")
async def get_gap_job():
    from data_engine.gap_job import gap_fill_job
    return gap_fill_job.snapshot()


@router.post("/gaps/job/stop", summary="停止缺口补齐任务")
async def stop_gap_job():
    from data_engine.gap_job import gap_fill_job
    return gap_fill_job.stop()


@router.get("/schedulers", summary="全部定时调度器的统一状态")
async def get_schedulers():
    """4 个调度器（A股主链 / 港美股 / crypto / 新闻）一次看全。

    此前只有 A 股主链在前端可见可关 —— 港美股 job 明明有独立 cron，
    界面上既看不到也关不掉。
    """
    def _work():
        from data_engine.scheduler_registry import all_scheduler_status
        return all_scheduler_status()
    try:
        return await asyncio.to_thread(_work)
    except Exception as e:  # noqa: BLE001
        logger.error(f"调度器状态查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schedulers/{scheduler_id}/toggle", summary="逐个开关调度器")
async def toggle_one_scheduler(scheduler_id: str, request: SchedulerToggleRequest):
    from data_engine.scheduler_registry import set_scheduler_enabled
    try:
        return await asyncio.to_thread(
            set_scheduler_enabled, scheduler_id, request.enabled,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"未知调度器: {scheduler_id}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"切换调度器 {scheduler_id} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calendar", summary="各市场交易日历的建设情况")
async def get_calendar_status():
    """判缺口的前提。日历没建起来 → 缺口只能标 suspected，不会自动补。"""
    def _work():
        from data_engine.trading_calendar import calendar_status
        session = get_session()
        try:
            return calendar_status(session)
        finally:
            session.close()
    try:
        return await asyncio.to_thread(_work)
    except Exception as e:  # noqa: BLE001
        logger.error(f"交易日历状态查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/limit-up/detail", summary="涨停池详情（含完整股票清单，供卡片展开查看）")
async def get_limit_up_detail(trade_date: Optional[str] = None):
    """数据监控页「涨停池」卡片展开详情用：完整连板榜+炸板榜（不是聚合数字）。"""
    try:
        from limit_up_engine.service import get_pool_overview
        return await asyncio.to_thread(get_pool_overview, trade_date, include_zhaban=True)
    except HTTPException:
        raise
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
