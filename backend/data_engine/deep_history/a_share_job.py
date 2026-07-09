"""
A股深历史日线回补 —— 常驻后台任务。

把 `daily_updater.py` 现成的 EastMoneyCrawler + ProxyPool + ThreadPoolExecutor 并发模型
搬过来做"往前补历史缺口"（按 MIN(date) 判断），而不是它原本"往后补增量"（按 MAX(date)）。

断点续跑：DB 的 min(date) 是主要真相来源；进度文件只缓存"确认过的 min_date"，
避免对已经补到数据源上限（上市首日/数据源本身没有更早数据）的股票重复发请求——
东财 K 线接口对超长跨度不会截断（已实测 1990~2009 一次性拿全），所以每只股票
理论上只需要成功请求一次。
"""
import json
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import func

from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo, DailyQuote
from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes, klines_to_records
from data_engine.liveness import LivenessTracker
from net.proxy_pool import ProxyPool, is_proxy_connect_error

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from eastmoney_crawler import EastMoneyCrawler, CrawlerConfig, parse_kline_data, ProxyTimeoutError  # noqa: E402
from proxy_manager import ProxyManager  # noqa: E402

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


class AShareDeepHistoryJob:
    """A股深历史日线回补的单例后台任务。"""

    SWITCH_IP_EVERY = 450  # 深历史单次响应体积比增量更新大，比 daily_updater 的 800 调低

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._state_lock = threading.Lock()
        self._reset_state()

    def _reset_state(self) -> None:
        self.status = "idle"  # idle / running / stopping / stopped / done
        self.total_all = 0
        self.done_total = 0
        self.processed_this_run = 0
        self.success = 0
        self.failed = 0
        self.new_records = 0
        self.current_symbol: Optional[str] = None
        self.recent: List[dict] = []
        self.started_at: Optional[float] = None
        self.config: Dict = {}
        self.proxy_state: str = "closed"
        self.abort_reason: Optional[str] = None
        self.last_activity_ago_seconds: float = 0.0

    # ---------- 对外：起停 + 查状态 ----------

    def start(
        self,
        *,
        workers: Optional[int] = None,
        symbols: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "message": "已经在跑了", **self.snapshot()}

            self._stop_flag.clear()
            self._reset_state()
            self.config = {"workers": workers, "symbols": symbols, "limit": limit}
            self.status = "running"
            self.started_at = time.time()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return {"ok": True, "message": "已启动", **self.snapshot()}

    def stop(self) -> dict:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return {"ok": False, "message": "当前没有在跑的任务", **self.snapshot()}
            self._stop_flag.set()
            with self._state_lock:
                self.status = "stopping"
            return {"ok": True, "message": "已发送停止信号，会在当前在途请求处理完后停下", **self.snapshot()}

    def snapshot(self) -> dict:
        with self._state_lock:
            elapsed = (time.time() - self.started_at) if self.started_at else 0.0
            rate_per_min = (self.processed_this_run / elapsed * 60) if elapsed > 0 else 0.0
            remaining = max(0, self.total_all - self.done_total)
            eta_s = (remaining / (rate_per_min / 60)) if rate_per_min > 0 else None
            return {
                "status": self.status,
                "total_all": self.total_all,
                "done_total": self.done_total,
                "processed_this_run": self.processed_this_run,
                "success": self.success,
                "failed": self.failed,
                "new_records": self.new_records,
                "current_symbol": self.current_symbol,
                "rate_per_min": round(rate_per_min, 1),
                "eta_seconds": eta_s,
                "elapsed_seconds": elapsed,
                "recent": list(self.recent),
                "config": dict(self.config),
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

    # ---------- 后台线程实体 ----------

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

    def _run(self) -> None:
        cfg = self.config
        try:
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

            proxy_mgr = ProxyManager()
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
                slot = pool.acquire()
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
        except Exception as e:  # noqa: BLE001 — 后台线程异常绝不能悄悄死掉不留痕迹
            logger.exception(f"A股深历史回补线程异常退出: {e}")
            with self._state_lock:
                self.status = "stopped"
                self.current_symbol = None


# 模块级单例
a_share_deep_history_job = AShareDeepHistoryJob()
