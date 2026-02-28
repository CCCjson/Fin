"""
实时行情 API
"""
import asyncio
import json
import queue
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from data_engine.fetchers.realtime import fetch_a_share_realtime, fetch_index_realtime
from data_engine.storage.database import get_session
from data_engine.storage.models import RealtimeSnapshot

router = APIRouter(prefix="/realtime", tags=["实时行情"])


def _get_proxy() -> Optional[dict]:
    """尝试获取代理 IP（可选功能）"""
    try:
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        from proxy_manager import ProxyManager
        mgr = ProxyManager()
        proxy = mgr.fetch_one_proxy()
        if proxy:
            logger.info(f"使用代理: {proxy.ip}:{proxy.port}")
            return proxy.to_requests_proxies()
    except Exception as e:
        logger.debug(f"获取代理失败，使用直连: {e}")
    return None


@router.get("/quotes")
async def get_realtime_quotes(
    use_proxy: bool = Query(False, description="是否使用代理 IP"),
    sort_by: str = Query("change_pct", description="排序字段: change_pct, amount, turnover"),
    ascending: bool = Query(False, description="是否升序"),
    save: bool = Query(True, description="是否保存到数据库"),
):
    """
    获取全部 A股实时行情

    - 分页获取 5000+ 只股票，被限流自动切换代理
    - 可选代理 IP
    - 默认保存到 realtime_snapshots 表
    """
    sort_map = {
        "change_pct": "f3",
        "amount": "f6",
        "turnover": "f8",
        "volume": "f5",
        "price": "f2",
    }
    sort_field = sort_map.get(sort_by, "f3")

    proxies = _get_proxy() if use_proxy else None

    # 放入线程池执行，避免阻塞 async 事件循环
    loop = asyncio.get_event_loop()
    quotes = await loop.run_in_executor(
        None,
        lambda: fetch_a_share_realtime(
            proxies=proxies,
            sort_field=sort_field,
            ascending=ascending,
        ),
    )

    if not quotes:
        return {"success": False, "message": "获取行情数据失败", "data": [], "count": 0}

    # 计算统计数据
    up_count = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] > 0)
    down_count = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] < 0)
    flat_count = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] == 0)
    limit_up = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] >= 9.9)
    limit_down = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] <= -9.9)

    # 保存到数据库
    saved_count = 0
    if save:
        def _save():
            nonlocal saved_count
            try:
                snapshot_time = datetime.now()
                session = get_session()
                for q in quotes:
                    if q.get("price") is None:
                        continue
                    record = RealtimeSnapshot(
                        snapshot_time=snapshot_time,
                        symbol=q.get("symbol", ""),
                        name=q.get("name", ""),
                        price=q.get("price"),
                        change_pct=q.get("change_pct"),
                        change_amount=q.get("change_amount"),
                        volume=q.get("volume"),
                        amount=q.get("amount"),
                        amplitude=q.get("amplitude"),
                        turnover=q.get("turnover"),
                        pe_ratio=q.get("pe_ratio"),
                        high=q.get("high"),
                        low=q.get("low"),
                        open=q.get("open"),
                        prev_close=q.get("prev_close"),
                    )
                    session.add(record)
                    saved_count += 1
                session.commit()
                session.close()
                logger.info(f"实时行情已保存: {saved_count} 条 (快照时间: {snapshot_time})")
            except Exception as e:
                logger.error(f"保存实时行情失败: {e}")

        await loop.run_in_executor(None, _save)

    return {
        "success": True,
        "count": len(quotes),
        "saved_count": saved_count,
        "statistics": {
            "total": len(quotes),
            "up": up_count,
            "down": down_count,
            "flat": flat_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
        },
        "data": quotes,
    }


def _compute_statistics(quotes: list) -> dict:
    """计算市场统计数据"""
    up = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] > 0)
    down = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] < 0)
    flat = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] == 0)
    limit_up = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] >= 9.9)
    limit_down = sum(1 for q in quotes if q.get("change_pct") is not None and q["change_pct"] <= -9.9)
    return {
        "total": len(quotes),
        "up": up, "down": down, "flat": flat,
        "limit_up": limit_up, "limit_down": limit_down,
    }


def _save_quotes(quotes: list) -> int:
    """保存行情到数据库，返回保存条数"""
    saved_count = 0
    try:
        snapshot_time = datetime.now()
        session = get_session()
        for q in quotes:
            if q.get("price") is None:
                continue
            record = RealtimeSnapshot(
                snapshot_time=snapshot_time,
                symbol=q.get("symbol", ""),
                name=q.get("name", ""),
                price=q.get("price"),
                change_pct=q.get("change_pct"),
                change_amount=q.get("change_amount"),
                volume=q.get("volume"),
                amount=q.get("amount"),
                amplitude=q.get("amplitude"),
                turnover=q.get("turnover"),
                pe_ratio=q.get("pe_ratio"),
                high=q.get("high"),
                low=q.get("low"),
                open=q.get("open"),
                prev_close=q.get("prev_close"),
            )
            session.add(record)
            saved_count += 1
        session.commit()
        session.close()
        logger.info(f"实时行情已保存: {saved_count} 条 (快照时间: {snapshot_time})")
    except Exception as e:
        logger.error(f"保存实时行情失败: {e}")
    return saved_count


@router.get("/quotes/stream")
async def stream_realtime_quotes(
    sort_by: str = Query("change_pct", description="排序字段"),
    ascending: bool = Query(False, description="是否升序"),
    save: bool = Query(True, description="是否保存到数据库"),
    use_proxy: bool = Query(False, description="是否使用代理 IP"),
):
    """
    流式获取全 A股实时行情（NDJSON）

    事件类型:
    - progress: 获取进度 {fetched, total, percent}
    - saving: 正在保存到数据库
    - done: 完成 {data, statistics, count, saved_count}
    - error: 失败 {message}
    """
    sort_map = {
        "change_pct": "f3", "amount": "f6", "turnover": "f8",
        "volume": "f5", "price": "f2",
    }
    sort_field = sort_map.get(sort_by, "f3")

    async def _streaming():
        loop = asyncio.get_event_loop()
        progress_queue: queue.Queue = queue.Queue()

        def _on_progress(fetched: int, total: int, percent: int, _scale: int):
            progress_queue.put((fetched, total, percent))

        def _fetch():
            return fetch_a_share_realtime(
                sort_field=sort_field,
                ascending=ascending,
                progress_callback=_on_progress,
            )

        # 启动后台线程获取数据
        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_fetch)

        def _drain_queue():
            """排空队列，返回最新进度"""
            latest = None
            while not progress_queue.empty():
                try:
                    latest = progress_queue.get_nowait()
                except queue.Empty:
                    break
            return latest

        # 持续轮询进度队列 + 心跳保活
        last_percent = -1
        heartbeat_counter = 0
        while not future.done():
            await asyncio.sleep(0.5)
            latest = _drain_queue()
            if latest:
                fetched, total, percent = latest
                percent = max(0, min(percent, 100))
                if percent != last_percent:
                    last_percent = percent
                    heartbeat_counter = 0
                    yield json.dumps({
                        "event": "progress",
                        "fetched": fetched,
                        "total": total,
                        "percent": percent,
                    }, ensure_ascii=False) + "\n"
            else:
                # 无新进度时每 5 秒发心跳（保持连接活跃）
                heartbeat_counter += 1
                if heartbeat_counter >= 10:
                    heartbeat_counter = 0
                    yield json.dumps({"event": "heartbeat"}, ensure_ascii=False) + "\n"

        # future 完成后，最后再排空一次队列
        latest = _drain_queue()
        if latest:
            fetched, total, percent = latest
            percent = max(0, min(percent, 100))
            if percent != last_percent:
                yield json.dumps({
                    "event": "progress",
                    "fetched": fetched,
                    "total": total,
                    "percent": percent,
                }, ensure_ascii=False) + "\n"

        # 获取结果
        try:
            quotes = future.result()
        except Exception as e:
            logger.error(f"获取行情异常: {e}")
            yield json.dumps({"event": "error", "message": str(e)}, ensure_ascii=False) + "\n"
            executor.shutdown(wait=False)
            return
        finally:
            executor.shutdown(wait=False)

        if not quotes:
            yield json.dumps({"event": "error", "message": "获取行情数据失败"}, ensure_ascii=False) + "\n"
            return

        statistics = _compute_statistics(quotes)

        # 保存到数据库
        saved_count = 0
        if save:
            yield json.dumps({"event": "saving", "message": "正在保存到数据库..."}, ensure_ascii=False) + "\n"
            await asyncio.sleep(0)
            saved_count = await loop.run_in_executor(None, _save_quotes, quotes)

        yield json.dumps({
            "event": "done",
            "count": len(quotes),
            "saved_count": saved_count,
            "statistics": statistics,
            "data": quotes,
        }, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/indices")
async def get_indices(
    use_proxy: bool = Query(False, description="是否使用代理 IP"),
):
    """获取大盘指数实时数据"""
    proxies = _get_proxy() if use_proxy else None

    loop = asyncio.get_event_loop()
    indices = await loop.run_in_executor(
        None,
        lambda: fetch_index_realtime(proxies=proxies),
    )

    if not indices:
        return {"success": False, "message": "获取指数数据失败", "data": []}

    return {
        "success": True,
        "data": indices,
    }


def _is_a_share_trading() -> bool:
    """判断 A 股是否在交易日的交易时间范围内（含午休）"""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    # 9:15 ~ 15:00 整个交易日（含集合竞价和午休）
    return 915 <= t <= 1500


# ======== Ticker 缓存 ========
_ticker_cache: dict = {"data": [], "is_trading": False, "updated_at": None}


def _refresh_ticker_cache() -> None:
    """刷新 ticker 缓存"""
    try:
        indices = fetch_index_realtime()
        is_trading = _is_a_share_trading()
        tz = timezone(timedelta(hours=8))
        _ticker_cache["data"] = indices or []
        _ticker_cache["is_trading"] = is_trading
        _ticker_cache["updated_at"] = datetime.now(tz).isoformat()
        logger.info(f"Ticker 缓存已刷新，{len(indices or [])} 条指数数据")
    except Exception as e:
        logger.error(f"Ticker 缓存刷新失败: {e}")


async def _ticker_scheduler() -> None:
    """后台定时任务：9:30 / 11:30 / 15:00 刷新 ticker"""
    tz = timezone(timedelta(hours=8))
    schedule_times = [(9, 30), (11, 30), (15, 0)]

    while True:
        now = datetime.now(tz)
        # 找到今天下一个触发时间
        next_run = None
        for h, m in schedule_times:
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate > now:
                next_run = candidate
                break
        # 今天的都过了，排到明天第一个
        if next_run is None:
            tomorrow = now + timedelta(days=1)
            h, m = schedule_times[0]
            next_run = tomorrow.replace(hour=h, minute=m, second=0, microsecond=0)

        wait_seconds = (next_run - now).total_seconds()
        logger.info(f"Ticker 下次刷新: {next_run.strftime('%Y-%m-%d %H:%M')}（{wait_seconds:.0f}s 后）")
        await asyncio.sleep(wait_seconds)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _refresh_ticker_cache)


@router.on_event("startup")
async def _start_ticker_scheduler():
    """启动时立即拉一次，然后启动定时任务"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _refresh_ticker_cache)
    asyncio.create_task(_ticker_scheduler())


@router.get("/ticker")
async def get_ticker():
    """首页行情滚动条 — 直接读缓存，秒返回"""
    return {
        "success": True,
        "is_trading": _ticker_cache["is_trading"],
        "updated_at": _ticker_cache["updated_at"],
        "data": _ticker_cache["data"],
    }
