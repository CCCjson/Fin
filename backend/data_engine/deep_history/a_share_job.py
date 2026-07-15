"""
A股深历史日线回补 —— 常驻后台任务。

把 `daily_updater.py` 现成的 EastMoneyCrawler + ProxyPool + ThreadPoolExecutor 并发模型
搬过来做"往前补历史缺口"（按 MIN(date) 判断），而不是它原本"往后补增量"（按 MAX(date)）。

断点续跑：DB 的 min(date) 是主要真相来源；进度文件只缓存"确认过的 min_date"，
避免对已经补到数据源上限（上市首日/数据源本身没有更早数据）的股票重复发请求——
东财 K 线接口对超长跨度不会截断（已实测 1990~2009 一次性拿全），所以每只股票
理论上只需要成功请求一次。

生命周期骨架（单例线程 + 状态机 + start/stop/snapshot + self-heal + finally 兜底）
统一由 `data_engine.base_job.BaseSingletonJob` 承接，本类只实现业务体 `_execute`
（ProxyPool 多 worker 并发内核）与状态钩子。
"""
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import func

from data_engine.base_job import BaseSingletonJob
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo, DailyQuote
from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes, klines_to_records
from data_engine.liveness import LivenessTracker
from net import ProxyExhaustedError, ProxyManager, get_proxy_manager
from net.proxy_pool import ProxyPool, is_proxy_connect_error

from acquisition.markets.eastmoney_crawler import (
    CrawlerConfig,
    EastMoneyCrawler,
    ProxyTimeoutError,
    parse_kline_data,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROGRESS_FILE = _BACKEND_DIR / "scripts" / "deep_history_a_share_progress.json"

TARGET_START = "19900101"
TARGET_START_DATE = date(1990, 1, 1)
_FLUSH_EVERY = 1  # 每处理一只就刷盘，任务要跑很久，进度得能随时看/断电续跑


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("confirmed", {})
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"进度文件读取失败，重新开始: {e}")
    return {"confirmed": {}}


def _save_progress(progress: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    tmp.replace(PROGRESS_FILE)


class AShareDeepHistoryJob(BaseSingletonJob):
    """A股深历史日线回补的单例后台任务。"""

    JOB_NAME = "A股深历史回补"
    STOPPING_MSG = "已发送停止信号，会在当前在途请求处理完后停下"

    SWITCH_IP_EVERY = 450  # 深历史单次响应体积比增量更新大，比 daily_updater 的 800 调低

    def _reset_extra_state(self) -> None:
        self.success = 0
        self.failed = 0
        self.new_records = 0
        self.current_symbol: Optional[str] = None
        self.proxy_state: str = "closed"
        self.abort_reason: Optional[str] = None
        self.last_activity_ago_seconds: float = 0.0

    # ---------- 对外：起停（snapshot/stop 由基类提供）----------

    def start(
        self,
        *,
        workers: Optional[int] = None,
        symbols: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> dict:
        return self._launch({"workers": workers, "symbols": symbols, "limit": limit})

    # ---------- 状态钩子 ----------

    def _clear_current(self) -> None:
        self.current_symbol = None

    def _extra_snapshot_fields(self) -> dict:
        return {
            "success": self.success,
            "failed": self.failed,
            "new_records": self.new_records,
            "current_symbol": self.current_symbol,
            "proxy_state": self.proxy_state,
            "abort_reason": self.abort_reason,
            "last_activity_ago_seconds": round(self.last_activity_ago_seconds, 1),
        }

    @staticmethod
    def _resolve_worker_count(requested: Optional[int], proxy_mgr: ProxyManager) -> int:
        if not proxy_mgr.api_url:
            return 1
        workers = requested if requested else 4
        return max(1, min(workers, 6))

    # ---------- 后台线程实体（基类 _run 负责异常日志 + finally 兜底翻正）----------

    def _build_candidates(self, cfg: Dict) -> tuple:
        """返回 (candidates, already_done_count)"""
        # 测试模式（显式给了 symbols）提前过滤，避免每次都全表扫描
        # ~14.5M 行的 daily_quotes（MIN(date) GROUP BY 全市场实测要 30s+）
        wanted_symbols = cfg.get("symbols")

        session = get_session()
        try:
            # is_active=0（含 2026-07-09 Jason 拍板停用的全部 ETF）不进候选；
            # stock_type 再排一道 etf，防止将来列表重导入误激活后又被深历史抓回来
            stock_q = session.query(StockInfo).filter(
                StockInfo.market == "a_share",
                StockInfo.is_active == 1,
                StockInfo.stock_type != "etf",
            )
            if wanted_symbols:
                stock_q = stock_q.filter(StockInfo.symbol.in_(wanted_symbols))
            stocks = stock_q.all()
            stock_meta = {
                s.symbol: {"name": s.name, "stock_type": s.stock_type or "stock", "exchange": s.exchange}
                for s in stocks
            }

            min_dates: Dict[str, Optional[date]] = {}
            if wanted_symbols:
                # 少量 symbol 时按 symbol 等值查（走 idx_symbol_date 索引，毫秒级）；
                # GROUP BY + IN 在 ~1400万行的全表上实测不会走索引，要 30s+，
                # 加了 IN 过滤也一样慢，所以测试模式不能用它
                for sym in stock_meta:
                    d = session.query(func.min(DailyQuote.date)).filter(
                        DailyQuote.symbol == sym, DailyQuote.market == "a_share"
                    ).scalar()
                    if d:
                        min_dates[sym] = d
            else:
                rows = session.query(DailyQuote.symbol, func.min(DailyQuote.date)).filter(
                    DailyQuote.market == "a_share"
                ).group_by(DailyQuote.symbol).all()
                for sym, d in rows:
                    min_dates[sym] = d
        finally:
            session.close()

        progress = _load_progress()
        confirmed: Dict[str, str] = progress["confirmed"]

        all_symbols = list(stock_meta.keys())

        candidates = []
        already_done = 0
        for symbol in all_symbols:
            floor = min_dates.get(symbol)
            if floor is not None and floor <= TARGET_START_DATE:
                already_done += 1
                continue
            floor_str = floor.isoformat() if floor else None
            if confirmed.get(symbol) == floor_str:
                already_done += 1
                continue
            fetch_end = (floor - timedelta(days=1)).strftime("%Y%m%d") if floor else date.today().strftime("%Y%m%d")
            meta = stock_meta[symbol]
            candidates.append({
                "symbol": symbol, "code": symbol.split(".")[0],
                "fetch_end": fetch_end, "floor_str": floor_str,
                "name": meta["name"], "stock_type": meta["stock_type"], "exchange": meta["exchange"],
            })

        if cfg.get("limit"):
            candidates = candidates[:cfg["limit"]]

        return candidates, already_done, progress

    def _execute(self, cfg: Dict) -> None:
        candidates, already_done, progress = self._build_candidates(cfg)
        confirmed: Dict[str, str] = progress["confirmed"]

        with self._state_lock:
            self.total_all = len(candidates) + already_done
            self.done_total = already_done

        if not candidates:
            logger.info("A股深历史回补：没有需要回补的股票")
            with self._state_lock:
                self.status = "done"
            return

        proxy_mgr = get_proxy_manager() or ProxyManager()
        workers = self._resolve_worker_count(cfg.get("workers"), proxy_mgr)
        logger.info(f"A股深历史回补启动：候选 {len(candidates)} 只，已确认 {already_done} 只，{workers} workers")

        work_q: "queue.Queue" = queue.Queue()
        result_q: "queue.Queue" = queue.Queue()
        for c in candidates:
            work_q.put(c)

        pool = ProxyPool(size=workers, mgr=proxy_mgr, min_delay=0.3, max_delay=1.5)
        stop_event = threading.Event()

        def _worker():
            crawler = EastMoneyCrawler(CrawlerConfig(
                min_delay=0.3, max_delay=1.5, max_retries=0,
                retry_delay=0, timeout=10, rate_limit_pause=30.0,
            ))
            try:
                slot = pool.acquire()
            except ProxyExhaustedError:
                # 启动即无可用 IP：退出，绝不直连。主循环据 futures 全退 + stall/
                # 熔断兜底收尾（把未处理的计为失败）。
                logger.warning("深历史 worker 启动即无可用快代理 IP，退出")
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
                        # 轮换后仍没 IP（额度尽）→ 不直连，走 ProxyTimeoutError
                        # 失败路径（换 IP + 记失败），熔断随换 IP 失败收敛。
                        if not pool.direct_mode and slot.proxy is None:
                            raise ProxyTimeoutError("代理槽无可用 IP，拒绝降级直连")

                        secid_mkt = None
                        if item["stock_type"] in ("index", "etf"):
                            if item["exchange"] == "SH":
                                secid_mkt = 1
                            elif item["exchange"] in ("SZ", "BJ"):
                                secid_mkt = 0

                        data = crawler.fetch_stock_history(
                            item["code"], TARGET_START, item["fetch_end"],
                            proxies=slot.to_requests_proxies(), secid_market=secid_mkt,
                        )
                        klines = parse_kline_data(data) if data else []
                        pool.report_success(slot)
                        result_q.put({**item, "ok": True, "klines": klines})
                        on_ip += 1
                        if on_ip >= self.SWITCH_IP_EVERY:
                            pool.refresh(slot)
                            crawler.reset_session(proxies=slot.to_requests_proxies())
                            on_ip = 0
                    except ProxyTimeoutError as e:
                        pool.report_failure(slot, proxy_connect=is_proxy_connect_error(e))
                        pool.refresh(slot)
                        crawler.reset_session(proxies=slot.to_requests_proxies())
                        result_q.put({**item, "ok": False, "klines": []})
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"{item['symbol']} 深历史回补失败: {e}")
                        result_q.put({**item, "ok": False, "klines": []})
            except Exception as e:  # noqa: BLE001
                logger.error(f"深历史 worker 异常退出: {e}")
            finally:
                pool.release(slot)

        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="deep-hist-a")
        futures = [executor.submit(_worker) for _ in range(workers)]

        session = get_session()
        pending_records: List[Dict] = []
        processed = 0
        last_flush = time.time()
        tracker = LivenessTracker(stall_timeout=180.0)

        def _drain_work_queue() -> None:
            while not work_q.empty():
                try:
                    work_q.get_nowait()
                except queue.Empty:
                    break

        try:
            while processed < len(candidates):
                if self._stop_flag.is_set() and not stop_event.is_set():
                    stop_event.set()
                    _drain_work_queue()

                with self._state_lock:
                    self.proxy_state = pool.breaker_state
                    self.last_activity_ago_seconds = tracker.seconds_since_activity()

                if pool.breaker_state == "dead" and not stop_event.is_set():
                    missing = len(candidates) - processed
                    logger.error(f"深历史回补：代理池与直连均不可用，中止本次任务，{missing} 只计为失败")
                    with self._state_lock:
                        self.failed += missing
                        self.abort_reason = "proxy_pool_dead"
                    stop_event.set()
                    _drain_work_queue()
                    break

                try:
                    res = result_q.get(timeout=0.5)
                except queue.Empty:
                    if all(f.done() for f in futures) and work_q.empty():
                        break
                    if tracker.is_stalled() and not stop_event.is_set():
                        missing = len(candidates) - processed
                        logger.error(f"深历史回补：{tracker.stall_timeout:.0f}s 无任何 worker 活动，"
                                     f"中止本次任务，{missing} 只计为失败")
                        with self._state_lock:
                            self.failed += missing
                            self.abort_reason = "stalled"
                        stop_event.set()
                        _drain_work_queue()
                        break
                    continue

                tracker.touch()
                processed += 1
                symbol = res["symbol"]

                with self._state_lock:
                    self.processed_this_run += 1
                    self.current_symbol = symbol

                if res["ok"]:
                    klines = res["klines"]
                    if klines:
                        pending_records.extend(klines_to_records(symbol, "a_share", klines))
                    # 确认值必须是"这次请求后 DB 实际能到的最早日期"，不是请求前的旧 floor——
                    # klines 按日期升序，klines[0] 就是这次拿到的最早一天；拿到新数据时
                    # 如果还记旧 floor，下次重跑会误判"没确认过"又白问一次
                    new_floor_str = klines[0]["date"] if klines else res["floor_str"]
                    confirmed[symbol] = new_floor_str
                    with self._state_lock:
                        self.success += 1
                        self.done_total += 1
                        self.recent.insert(0, {
                            "symbol": symbol, "name": res["name"], "status": "ok",
                            "rows": len(klines), "at": time.strftime("%H:%M:%S"),
                        })
                        self.recent = self.recent[:30]
                else:
                    with self._state_lock:
                        self.failed += 1
                        self.recent.insert(0, {
                            "symbol": symbol, "name": res["name"], "status": "failed",
                            "rows": 0, "at": time.strftime("%H:%M:%S"),
                        })
                        self.recent = self.recent[:30]

                now = time.time()
                if pending_records and (
                    processed % 200 == 0 or now - last_flush > 2.0 or processed == len(candidates)
                ):
                    try:
                        n = bulk_upsert_quotes(session, pending_records)
                        with self._state_lock:
                            self.new_records += n
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"深历史批量写入失败（丢弃 {len(pending_records)} 条）: {e}")
                        try:
                            session.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                    pending_records = []
                    last_flush = now

                if processed % _FLUSH_EVERY == 0:
                    _save_progress(progress)
        finally:
            stop_event.set()
            executor.shutdown(wait=False)

        if pending_records:
            try:
                n = bulk_upsert_quotes(session, pending_records)
                with self._state_lock:
                    self.new_records += n
            except Exception as e:  # noqa: BLE001
                logger.error(f"深历史批量写入失败（丢弃 {len(pending_records)} 条）: {e}")

        session.close()
        _save_progress(progress)

        with self._state_lock:
            self.status = "stopped" if (self._stop_flag.is_set() or self.abort_reason) else "done"
            self.current_symbol = None
        logger.success(
            f"A股深历史回补结束（status={self.status}）：成功 {self.success}, "
            f"失败 {self.failed}, 新记录 {self.new_records}"
        )


# 模块级单例
a_share_deep_history_job = AShareDeepHistoryJob()
