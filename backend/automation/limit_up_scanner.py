"""
涨停盘中扫描 —— 开机自启的独立后台任务（同 price_alert_monitor.py 范式）。

盘中每隔 TRADING_INTERVAL 秒抓一次涨停/炸板池快照，写入进程内内存缓存（不入库——
盘中同一票可能封板→炸板→再封板反复，每次轮询都入库会产生冗余行，且和"盘后一天
一份定案快照"的 LimitUpPool 表语义冲突，见方案「阶段二」一节）。收盘后走
limit_up_engine.ingest 的正式流程落库成为当天定案记录。

只抓 zt(今日涨停)/zb(炸板) 两个池子（不抓 previous/dt），把盘中轮询的接口调用量
压到最低——涨停池接口是单次请求不需要翻页，但仍需实测代理池压力，轮询间隔先给
保守值（见方案风险点第6条），不要一上来就抄 price_alert_monitor 的 30 秒。
"""
import asyncio
import time
from datetime import datetime
from typing import Dict, Optional

from loguru import logger

from automation.scheduler import _is_trading_hours

TRADING_INTERVAL = 120  # 盘中轮询间隔（秒），保守值，代理开销待实测后再考虑调低
IDLE_INTERVAL = 300     # 非交易时间降频，仍保留最后一次盘中数据作为盘后过渡

_CACHE: Dict = {"data": None, "ts": 0.0}


def _scan_once() -> Dict:
    """同步：拉 zt/zb 两池 + 本地聚合成情绪摘要。在线程池执行（避免阻塞事件循环）。"""
    from data_engine.fetchers.limit_up import fetch_limit_up_pool, fetch_zhaban_pool

    trade_date_str = datetime.now().strftime("%Y%m%d")
    zt_rows = fetch_limit_up_pool(trade_date_str)
    try:
        zb_rows = fetch_zhaban_pool(trade_date_str)
    except ValueError:
        zb_rows = []

    ladder: Dict[str, int] = {}
    for row in zt_rows:
        boards = row.get("consecutive_boards")
        if boards is None:
            continue
        key = "7板+" if boards >= 7 else f"{boards}板"
        ladder[key] = ladder.get(key, 0) + 1

    zt_count, zb_count = len(zt_rows), len(zb_rows)
    total = zt_count + zb_count
    break_rate = (zb_count / total) if total > 0 else None

    top_boards = sorted(
        [r for r in zt_rows if r.get("consecutive_boards") is not None],
        key=lambda r: r["consecutive_boards"], reverse=True,
    )  # 不截断——widget 要展示完整清单，见 service.get_pool_overview 同款设计

    return {
        "trade_date": f"{trade_date_str[:4]}-{trade_date_str[4:6]}-{trade_date_str[6:]}",
        "snapshot_time": datetime.now().isoformat(),
        "provisional": True,  # 盘中数据，非当日定案
        "limit_up_count": zt_count,
        "break_count": zb_count,
        "break_rate": break_rate,
        "ladder_distribution": ladder,
        "top_boards": [{
            "symbol": r["symbol"], "name": r.get("name"),
            "consecutive_boards": r.get("consecutive_boards"),
            "seal_amount": r.get("seal_amount"), "industry": r.get("industry"),
            "zt_stat": f"{r.get('zt_stat_days')}/{r.get('zt_stat_count')}" if r.get("zt_stat_days") else None,
        } for r in top_boards],
    }


async def limit_up_scan_loop():
    """常驻后台任务：交易时间内定期扫描，非交易时间自动降频不空转。"""
    loop = asyncio.get_event_loop()
    while True:
        try:
            if _is_trading_hours():
                data = await loop.run_in_executor(None, _scan_once)
                _CACHE["data"] = data
                _CACHE["ts"] = time.time()
                await asyncio.sleep(TRADING_INTERVAL)
            else:
                await asyncio.sleep(IDLE_INTERVAL)
        except Exception as e:  # noqa: BLE001 — 常驻任务绝不能因单次失败退出
            logger.warning(f"[涨停盘中扫描] 单轮失败: {e}")
            await asyncio.sleep(TRADING_INTERVAL)


def get_intraday_snapshot(max_age_seconds: float = 600.0) -> Optional[Dict]:
    """供 limit_up_engine.service 调用：缓存新鲜则返回，否则 None（回退到 DB 数据）。"""
    data = _CACHE.get("data")
    if not data:
        return None
    if time.time() - _CACHE.get("ts", 0.0) > max_age_seconds:
        return None
    return data
