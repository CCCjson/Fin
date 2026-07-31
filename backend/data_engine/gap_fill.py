"""定点补齐 —— 「把 07-10 到 07-15 这几天的数据补回来」。

## 为什么现有 updater 补不了中间的洞

三个 updater 全是**前沿式**的：算出「库里最新那天」，从它的次日往后拉。

    DailyUpdater          latest >= target_date → already_fresh 跳过
    OverseasDailyUpdater  latest >= frontier    → skip
    CryptoUpdater         从 latest+1 起拉

这套逻辑对「尾部落后」完全正确，对**中间的洞**却是结构性失明的：库里 07-16 有数据，
`latest` 就是 07-16，07-10~07-15 那个洞永远进不了待更新列表。实测现场就有一个 ——
`us_stock` 的 2026-07-03 只有 5 行（正常日 11000+），而 07-06 之后一切正常，
所以每日增量永远不会回头看它一眼。

本模块提供**按日期区间**拉取的补齐路径，`registry` 里 `mode="gap_fill"` 时走这里。

## 代理铁律

A 股侧完整遵守 `docs/CODING_STANDARDS.md` §8.2：**任何场景都不许降级本地直连**。
没配快代理 → 直连合法；配了但取不到 IP → `ProxyExhaustedError`，一个请求都不发；
拿到 IP 后失败 → 换下一个。worker 内核照搬 `deep_history/a_share_job.py` 那套
（那是跑过很久、验过熔断/静默超时的实现）。

港美股走 `resolve_overseas_proxy()`，返回 None 就是合法直连 —— 铁律管的是国内通道。
"""
from __future__ import annotations

import os
import queue
import random
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from loguru import logger

from common.market import A_SHARE
from data_engine.events import make_event
from data_engine.liveness import LivenessTracker
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo

# 单次补洞最多跨多少天。安全阀：防止日历刚建好时一口气报出半年缺口、
# 然后触发一次几小时的全量重拉。超出的部分留给下一轮（缺口表还在，不会丢）。
MAX_GAP_SPAN_DAYS = int(os.getenv("GAP_FILL_MAX_SPAN_DAYS", "45"))

# A 股补洞的 worker 数上限。比深历史保守 —— 补洞是**计划外**的额外流量，
# 不该把代理额度一次抽干，影响当天正常的增量更新。
A_SHARE_MAX_WORKERS = int(os.getenv("GAP_FILL_A_SHARE_WORKERS", "6"))

# 每处理多少只换一次 IP（同 DailyUpdater.SWITCH_IP_EVERY 的量级）
_SWITCH_IP_EVERY = 800

# 港美股一批喂给 yfinance 多少只
_OVERSEAS_BATCH = int(os.getenv("GAP_FILL_OVERSEAS_BATCH", "100"))
_OVERSEAS_SLEEP = float(os.getenv("GAP_FILL_OVERSEAS_SLEEP", "3.0"))


def clamp_span(gap_dates: tuple[date, ...]) -> tuple[date, date]:
    """把要补的日期收成一个 `[start, end]` 区间，并钳在 `MAX_GAP_SPAN_DAYS` 内。

    补一个区间比补 N 个单日便宜得多（东财/yfinance 的历史接口本来就按区间拉，
    一次请求就能覆盖整段），所以哪怕中间夹着不缺的天也照样整段拉 —— 反正是
    upsert，重叠部分不会重复落行。
    """
    start, end = min(gap_dates), max(gap_dates)
    if (end - start).days > MAX_GAP_SPAN_DAYS:
        start = end - timedelta(days=MAX_GAP_SPAN_DAYS)
        logger.warning(
            f"[补洞] 区间跨度超过 {MAX_GAP_SPAN_DAYS} 天，本次只补 {start}~{end}，"
            f"其余留给下一轮（缺口表还在，不会丢）"
        )
    return start, end


# ====================================================================
# A 股
# ====================================================================


def fill_a_share_days(ctx) -> Iterator[dict]:
    """补 A 股指定日期区间的日线。

    内核照搬 `deep_history/a_share_job._execute` 的 ProxyPool 多 worker 模型
    （熔断 / 静默超时 / 换 IP 都是在那边跑出来的），差别只有三点：
      1. 拉的是 `[start, end]` 固定区间，不是「上市首日 ~ 库里最早那天」
      2. 候选是**全部活跃 A 股**（洞是全市场性的，不是个别票缺历史）
      3. 结果流式 yield 契约事件，不写进度文件（补洞是一次性的，不需要断点续跑）
    """
    from acquisition.markets.eastmoney_crawler import (
        CrawlerConfig,
        EastMoneyCrawler,
        ProxyTimeoutError,
        parse_kline_data,
    )
    from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes, klines_to_records
    from net import ProxyExhaustedError, ProxyManager, get_proxy_manager
    from net.proxy_pool import ProxyPool, is_proxy_connect_error

    key = "daily.a_share"
    if not ctx.gap_dates:
        yield make_event("skipped", key, A_SHARE, message="没有指定要补的日期")
        return

    start, end = clamp_span(ctx.gap_dates)
    start_str, end_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    session = get_session()
    try:
        stocks = session.query(StockInfo).filter(
            StockInfo.market == A_SHARE,
            StockInfo.is_active == 1,
            StockInfo.stock_type != "etf",   # 口径与 DailyUpdater / 深历史一致
        ).all()
        candidates = [
            {"symbol": s.symbol, "code": s.symbol.split(".")[0], "name": s.name,
             "stock_type": s.stock_type or "stock", "exchange": s.exchange}
            for s in stocks
        ]
    finally:
        session.close()

    if ctx.limit:
        candidates = candidates[:ctx.limit]
    total = len(candidates)
    if not total:
        yield make_event("skipped", key, A_SHARE, message="活跃 A 股列表为空")
        return

    proxy_mgr = get_proxy_manager() or ProxyManager()
    workers = min(A_SHARE_MAX_WORKERS, max(1, getattr(proxy_mgr, "max_slots", 4) or 4))
    logger.info(f"[补洞] A股 {start}~{end}：{total} 只，{workers} workers")
    yield make_event("start", key, A_SHARE, total=total,
                     note=f"补 {start}~{end}（{len(ctx.gap_dates)} 个交易日）")

    work_q: queue.Queue = queue.Queue()
    result_q: queue.Queue = queue.Queue()
    for c in candidates:
        work_q.put(c)

    pool = ProxyPool(size=workers, mgr=proxy_mgr, min_delay=0.3, max_delay=1.5)
    stop_event = threading.Event()

    def _worker() -> None:
        crawler = EastMoneyCrawler(CrawlerConfig(
            min_delay=0.3, max_delay=1.5, max_retries=0,
            retry_delay=0, timeout=10, rate_limit_pause=30.0,
        ))
        try:
            slot = pool.acquire()
        except ProxyExhaustedError:
            # 启动即无可用 IP：退出，**绝不直连**。主循环靠 futures 全退 + 熔断兜底收尾。
            logger.warning("[补洞] A股 worker 启动即无可用快代理 IP，退出")
            return
        crawler._warm_up(proxies=slot.to_requests_proxies())
        on_ip = 0
        try:
            while not stop_event.is_set():
                try:
                    item = work_q.get(timeout=0.5)
                except queue.Empty:
                    return
                try:
                    if slot.proxy is not None and slot.proxy.is_expired:
                        pool.refresh(slot)
                        crawler.reset_session(proxies=slot.to_requests_proxies())
                    if not pool.direct_mode and slot.proxy is None:
                        raise ProxyTimeoutError("代理槽无可用 IP，拒绝降级直连")

                    secid_mkt = None
                    if item["stock_type"] in ("index", "etf"):
                        if item["exchange"] == "SH":
                            secid_mkt = 1
                        elif item["exchange"] in ("SZ", "BJ"):
                            secid_mkt = 0

                    data = crawler.fetch_stock_history(
                        item["code"], start_str, end_str,
                        proxies=slot.to_requests_proxies(), secid_market=secid_mkt,
                    )
                    klines = parse_kline_data(data) if data else []
                    pool.report_success(slot)
                    result_q.put({**item, "ok": True, "klines": klines})
                    on_ip += 1
                    if on_ip >= _SWITCH_IP_EVERY:
                        pool.refresh(slot)
                        crawler.reset_session(proxies=slot.to_requests_proxies())
                        on_ip = 0
                except ProxyTimeoutError as e:
                    pool.report_failure(slot, proxy_connect=is_proxy_connect_error(e))
                    pool.refresh(slot)
                    crawler.reset_session(proxies=slot.to_requests_proxies())
                    result_q.put({**item, "ok": False, "klines": []})
                except Exception as e:  # noqa: BLE001 — 单只失败不拖垮整轮
                    logger.warning(f"[补洞] {item['symbol']} 失败: {e}")
                    result_q.put({**item, "ok": False, "klines": []})
        except Exception as e:  # noqa: BLE001
            logger.error(f"[补洞] A股 worker 异常退出: {e}")
        finally:
            pool.release(slot)

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gapfill-a")
    futures = [executor.submit(_worker) for _ in range(workers)]

    session = get_session()
    pending: list[dict] = []
    processed = success = failed = rows = 0
    abort_reason: str | None = None
    tracker = LivenessTracker(stall_timeout=180.0)
    last_emit = time.time()

    def _drain() -> None:
        while not work_q.empty():
            try:
                work_q.get_nowait()
            except queue.Empty:
                break

    try:
        while processed < total:
            if ctx.should_stop() and not stop_event.is_set():
                abort_reason = "stopped"
                stop_event.set()
                _drain()

            if pool.breaker_state == "dead" and not stop_event.is_set():
                failed += total - processed
                abort_reason = "proxy_pool_dead"
                logger.error("[补洞] A股：代理池与直连均不可用，中止")
                stop_event.set()
                _drain()
                break

            try:
                res = result_q.get(timeout=0.5)
            except queue.Empty:
                if all(f.done() for f in futures) and work_q.empty():
                    break
                if tracker.is_stalled() and not stop_event.is_set():
                    failed += total - processed
                    abort_reason = "stalled"
                    logger.error(f"[补洞] A股：{tracker.stall_timeout:.0f}s 无活动，中止")
                    stop_event.set()
                    _drain()
                    break
                continue

            tracker.touch()
            processed += 1
            if res["ok"]:
                success += 1
                if res["klines"]:
                    pending.extend(klines_to_records(res["symbol"], A_SHARE, res["klines"]))
            else:
                failed += 1

            if pending and (processed % 200 == 0 or processed == total):
                try:
                    rows += bulk_upsert_quotes(session, pending)
                except Exception as e:  # noqa: BLE001 — 一批坏数据不该掀翻整轮
                    session.rollback()
                    logger.warning(f"[补洞] A股写库失败（丢弃 {len(pending)} 行）: {e}")
                pending.clear()

            now = time.time()
            if now - last_emit > 1.0 or processed == total:
                last_emit = now
                yield make_event("progress", key, A_SHARE, total=total, current=processed,
                                 updated=success, failed=failed, records=rows,
                                 proxy_state=pool.breaker_state)

        if pending:
            try:
                rows += bulk_upsert_quotes(session, pending)
            except Exception as e:  # noqa: BLE001
                session.rollback()
                logger.warning(f"[补洞] A股尾批写库失败: {e}")
    finally:
        stop_event.set()
        _drain()
        executor.shutdown(wait=False)
        session.close()

    logger.success(f"[补洞] A股 {start}~{end} 完成: 成功 {success} / 失败 {failed} / {rows} 行")
    yield make_event("complete", key, A_SHARE, total=total, current=processed,
                     updated=success, failed=failed, records=rows,
                     aborted=bool(abort_reason), abort_reason=abort_reason,
                     note=f"补 {start}~{end}")


# ====================================================================
# 港美股
# ====================================================================


def fill_overseas_days(market: str, ctx) -> Iterator[dict]:
    """补港/美股指定日期区间的日线。

    比 A 股便宜得多：yfinance 一次能拉一批 symbol 的一整段区间，
    `_OVERSEAS_BATCH` 只 100 只/批，几千只票十几批就完事。

    ⚠️ `end` 要 **+1 天**：yfinance 的 `start`/`end` 是**左闭右开**的，
    传 end=07-15 拿不到 07-15 那根。这个坑不写出来下次一定再踩。
    """
    from acquisition.markets.yf_batch import download_daily_range, yahoo_job_lock
    from common.market import to_yf_symbol
    from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes, yf_df_to_records
    from data_engine.overseas_daily_updater import _TRADABLE_TYPES

    key = f"daily.{market}"
    if not ctx.gap_dates:
        yield make_event("skipped", key, market, message="没有指定要补的日期")
        return

    start, end = clamp_span(ctx.gap_dates)

    session = get_session()
    try:
        symbols = [s.symbol for s in session.query(StockInfo).filter(
            StockInfo.market == market,
            StockInfo.is_active == 1,
            StockInfo.stock_type.in_(_TRADABLE_TYPES),
        ).all()]
    finally:
        session.close()

    if ctx.limit:
        symbols = symbols[:ctx.limit]
    total = len(symbols)
    if not total:
        yield make_event("skipped", key, market, message=f"{market} 活跃标的列表为空")
        return

    # 深历史回补也在打 Yahoo，同时跑等于双倍请求量，两边都可能被限速
    with yahoo_job_lock(f"补洞-{market}") as ok:
        if not ok:
            yield make_event("skipped", key, market,
                             message="深历史回补正在跑（Yahoo 长任务互斥），本次跳过")
            return

        logger.info(f"[补洞] {market} {start}~{end}：{total} 只")
        yield make_event("start", key, market, total=total,
                         note=f"补 {start}~{end}（{len(ctx.gap_dates)} 个交易日）")

        from data_engine.deep_history.overseas_job import _call_with_timeout

        session = get_session()
        updated = failed = rows = processed = 0
        # yfinance 的 end 是左闭右开 —— 不 +1 天就拿不到 end 当天那根
        start_str = start.isoformat()
        end_str = (end + timedelta(days=1)).isoformat()
        try:
            for i in range(0, total, _OVERSEAS_BATCH):
                if ctx.should_stop():
                    logger.info(f"[补洞] {market} 收到停止信号，已处理 {processed}/{total}")
                    break
                batch = symbols[i:i + _OVERSEAS_BATCH]
                yf_symbols = [
                    to_yf_symbol(s) if market == "hk_stock" else s for s in batch
                ]
                restore = dict(zip(yf_symbols, batch, strict=True))
                try:
                    df = _call_with_timeout(
                        lambda ys=yf_symbols: download_daily_range(ys, start_str, end_str),
                        180.0, f"补洞 {market} batch{i // _OVERSEAS_BATCH}",
                    )
                except Exception as e:  # noqa: BLE001 — 单批失败不拖垮整轮
                    failed += len(batch)
                    processed += len(batch)
                    logger.warning(f"[补洞] {market} 批 {i // _OVERSEAS_BATCH} 失败: {e}")
                    yield make_event("progress", key, market, total=total, current=processed,
                                     updated=updated, failed=failed, records=rows)
                    continue

                records: list[dict] = []
                for yf_sym in yf_symbols:
                    orig = restore[yf_sym]
                    try:
                        sub = df[yf_sym] if len(yf_symbols) > 1 else df
                        sub = sub.dropna(how="all")
                    except (KeyError, IndexError, TypeError):
                        continue
                    if sub is None or sub.empty:
                        continue
                    recs = yf_df_to_records(orig, market, sub)
                    if recs:
                        records.extend(recs)
                        updated += 1
                if records:
                    try:
                        rows += bulk_upsert_quotes(session, records)
                    except Exception as e:  # noqa: BLE001
                        session.rollback()
                        failed += len(batch)
                        logger.warning(f"[补洞] {market} 写库失败（丢弃 {len(records)} 行）: {e}")

                processed += len(batch)
                yield make_event("progress", key, market, total=total, current=processed,
                                 updated=updated, failed=failed, records=rows)
                if i + _OVERSEAS_BATCH < total:
                    time.sleep(_OVERSEAS_SLEEP + random.uniform(0, 1))
        finally:
            session.close()

    logger.success(f"[补洞] {market} {start}~{end} 完成: 更新 {updated} 只 / {rows} 行 / 失败 {failed}")
    yield make_event("complete", key, market, total=total, current=processed,
                     updated=updated, failed=failed, records=rows,
                     note=f"补 {start}~{end}")
