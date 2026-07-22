"""
涨停盘中快照 —— **按需扫描**，不再是开机自启的常驻后台任务。

盘中抓一次涨停/炸板池写进程内内存缓存（不入库——盘中同一票可能封板→炸板→再封板
反复，每次都入库会产生冗余行，且和「盘后一天一份定案快照」的 LimitUpPool 表语义
冲突）。收盘后走 limit_up_engine.ingest 的正式流程落库成为当天定案记录。

2026-07-10：常驻轮询（每 120s，240 次/天）已删。它没有任何开关，连
FIN_DISABLE_SCHEDULERS 都管不到，没人看的时候也在烧快代理额度。现在由
`get_intraday_snapshot()` 在缓存过期时懒扫，即 MoneyBill 问了才出网。

只抓 zt(今日涨停)/zb(炸板) 两个池子（不抓 previous/dt）。
"""
import time
from datetime import datetime
from typing import Dict, Optional

from loguru import logger

from automation.trading_hours import _is_trading_hours
from common.market import A_SHARE
from common.market_time import market_today

TRADING_INTERVAL = 120  # 盘中快照的缓存有效期（秒）：120s 内重复问不再出网

_CACHE: Dict = {"data": None, "ts": 0.0}


def _scan_once() -> Dict:
    """同步：拉 zt/zb 两池 + 本地聚合成情绪摘要。在线程池执行（避免阻塞事件循环）。"""
    from acquisition.markets.limit_up import fetch_limit_up_pool, fetch_zhaban_pool

    trade_date_str = market_today(A_SHARE).strftime("%Y%m%d")
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


def get_intraday_snapshot(max_age_seconds: float = TRADING_INTERVAL) -> Optional[Dict]:
    """供 limit_up_engine.service 调用：缓存新鲜就直接返回，过期则**现扫一次**。

    以前靠 `limit_up_scan_loop` 常驻后台每 120s 无条件扫（240 次/天，全在烧快代理
    额度，哪怕没人看）。改成懒扫：MoneyBill 问「今天涨停多少家」时才出网。

    非交易时段不扫——盘后走 `limit_up_engine.ingest` 落库，DB 里有定案数据。
    扫描失败返回上一次的陈旧快照（有总比没有强），全都没有才返回 None。
    """
    data = _CACHE.get("data")
    fresh = data and (time.time() - _CACHE.get("ts", 0.0) <= max_age_seconds)
    if fresh or not _is_trading_hours():
        return data if fresh else None

    try:
        data = _scan_once()
    except Exception as e:  # noqa: BLE001 — 抓取失败不该让 get_limit_up_pool 整个炸掉
        logger.warning(f"[涨停按需扫描] 失败: {e}")
        return _CACHE.get("data")   # 退回陈旧快照

    _CACHE["data"] = data
    _CACHE["ts"] = time.time()
    return data
