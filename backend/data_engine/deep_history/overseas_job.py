"""
港股/美股深历史日线回补 —— 常驻后台任务。

跟 A股 深历史（AShareDeepHistoryJob）不同，港美股是"从 0 到有"，走 yfinance
`yf.download()` 批量下载而不是 EastMoneyCrawler+代理池模型。一次只能跑一个
market（hk_stock/us_stock 共用同一个单例），避免两个方向同时叠加 Yahoo 限速风险。

生命周期骨架（单例线程 + 状态机 + start/stop/snapshot + self-heal + finally 兜底）
统一由 `data_engine.base_job.BaseSingletonJob` 承接，本类只实现业务体 `_execute`
（yfinance 分批下载）与状态钩子。
"""
import json
import random
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, TypeVar

from loguru import logger

from data_engine.base_job import BaseSingletonJob
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo, DailyQuote
from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes
from data_engine.deep_history.us_filter import classify_and_persist_us_universe
from common.market import to_yf_symbol

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROGRESS_FILE = _BACKEND_DIR / "scripts" / "deep_history_overseas_progress.json"

FETCH_START = {"hk_stock": "1990-01-01", "us_stock": "1970-01-01"}
_FLUSH_EVERY_BATCH = 1
BATCH_FETCH_TIMEOUT = 180.0
SINGLE_FETCH_TIMEOUT = 60.0

_T = TypeVar("_T")


def _call_with_timeout(fn: Callable[[], _T], timeout: float, label: str) -> _T:
    """在独立线程里跑 fn 并最多等待 timeout 秒；超时抛 TimeoutError。

    yfinance 没有原生请求超时参数（0.2.x 的 curl_cffi 路线下注入自定义
    requests session 不可靠），用线程+join 兜底：超时后原线程直接放弃
    （daemon 线程泄漏无害——yfinance 请求最终会完成或报错，只是没人
    再等它），避免单批/单只请求无限期挂起，把整个回补任务的 /status
    冻结在原地。
    """
    box: List = []
    err: List[BaseException] = []

    def _target() -> None:
        try:
            box.append(fn())
        except BaseException as e:  # noqa: BLE001 — 把异常带回调用线程重新抛出
            err.append(e)

    t = threading.Thread(target=_target, daemon=True, name=f"yf-timeout-{label}")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"{label} 超过 {timeout:.0f}s 未返回，判定挂起")
    if err:
        raise err[0]
    return box[0]


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("hk_stock", {}).setdefault("confirmed_no_data", [])
                data.setdefault("us_stock", {}).setdefault("confirmed_no_data", [])
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"进度文件读取失败，重新开始: {e}")
    return {"hk_stock": {"confirmed_no_data": []}, "us_stock": {"confirmed_no_data": []}}


def _save_progress(progress: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    tmp.replace(PROGRESS_FILE)


def _chunks(items: List, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class OverseasDeepHistoryJob(BaseSingletonJob):
    """港股/美股深历史日线回补的单例后台任务（market 参数区分）。"""

    JOB_NAME = "海外深历史回补"
    ALREADY_RUNNING_MSG = "已经在跑了（港股/美股共用一个任务，同时只能跑一个）"
    STOPPING_MSG = "已发送停止信号，会在当前批次处理完后停下"

    def _reset_extra_state(self) -> None:
        self.market: Optional[str] = None
        self.success = 0
        self.no_data = 0
        self.failed_batches = 0
        self.new_records = 0
        self.excluded: Dict = {}  # {stock_type: count}，仅 us_stock 有意义
        self.current_batch: List[str] = []
        self.last_activity_at: Optional[float] = time.time()

    # ---------- 对外：起停（snapshot/stop 由基类提供）----------

    def start(
        self,
        *,
        market: str,
        symbols: Optional[List[str]] = None,
        limit: Optional[int] = None,
        batch_size: int = 50,
        sleep_between_batches: float = 3.0,
        max_retry: int = 3,
    ) -> dict:
        if market not in ("hk_stock", "us_stock"):
            return {"ok": False, "message": f"不支持的市场: {market}"}
        return self._launch({
            "market": market, "symbols": symbols, "limit": limit,
            "batch_size": batch_size, "sleep_between_batches": sleep_between_batches,
            "max_retry": max_retry,
        })

    # ---------- 状态钩子 ----------

    def _clear_current(self) -> None:
        self.current_batch = []

    def _extra_snapshot_fields(self) -> dict:
        return {
            "market": self.market,
            "success": self.success,
            "no_data": self.no_data,
            "failed_batches": self.failed_batches,
            "new_records": self.new_records,
            "excluded": dict(self.excluded),
            "current_batch": list(self.current_batch),
            "last_activity_ago_seconds": round(time.time() - self.last_activity_at, 1)
            if self.last_activity_at else None,
        }

    # ---------- 后台线程实体（基类 _run 负责异常日志 + finally 兜底翻正）----------

    def _resolve_universe(self, market: str, cfg: Dict) -> tuple:
        """返回 (todo_symbols, already_done_count, progress_dict)"""
        session = get_session()
        try:
            if market == "us_stock":
                counts = classify_and_persist_us_universe()
                with self._state_lock:
                    self.excluded = {
                        "excluded_bond_note": counts.get("excluded_bond_note", 0),
                        "excluded_leveraged_etf": counts.get("excluded_leveraged_etf", 0),
                    }
                rows = session.query(StockInfo.symbol).filter(
                    StockInfo.market == "us_stock",
                    StockInfo.stock_type.in_(["stock", "etf"]),
                ).all()
            else:
                rows = session.query(StockInfo.symbol).filter(StockInfo.market == market).all()
            universe = [r[0] for r in rows]

            done_rows = session.query(DailyQuote.symbol).filter(
                DailyQuote.market == market
            ).distinct().all()
            done_set = {r[0] for r in done_rows}
        finally:
            session.close()

        progress = _load_progress()
        confirmed_no_data = set(progress[market]["confirmed_no_data"])

        if cfg.get("symbols"):
            wanted = set(cfg["symbols"])
            universe = [s for s in universe if s in wanted]

        already_done = sum(1 for s in universe if s in done_set or s in confirmed_no_data)
        todo = [s for s in universe if s not in done_set and s not in confirmed_no_data]

        if cfg.get("limit"):
            todo = todo[:cfg["limit"]]

        return todo, already_done, progress

    def _fetch_batch(self, market: str, batch: List[str], fetch_start: str):
        """返回 {symbol: DataFrame}；异常向上抛出由调用方重试"""
        from acquisition.markets.yf_batch import download_daily_history

        yf_symbols = [to_yf_symbol(s) if market == "hk_stock" else s for s in batch]
        restore = dict(zip(yf_symbols, batch))

        df = download_daily_history(yf_symbols, fetch_start)

        result: Dict[str, "object"] = {}
        for yf_sym in yf_symbols:
            orig = restore[yf_sym]
            try:
                sub = df[yf_sym] if len(yf_symbols) > 1 else df
                sub = sub.dropna(how="all")
            except (KeyError, IndexError, TypeError):
                sub = None
            result[orig] = sub
        return result

    def _fetch_single(self, market: str, symbol: str, fetch_start: str):
        """单只兜底（排除批量下载的假阴性）"""
        from acquisition.markets.yf_batch import fetch_daily_history

        yf_sym = to_yf_symbol(symbol) if market == "hk_stock" else symbol
        return fetch_daily_history(yf_sym, fetch_start)

    @staticmethod
    def _df_to_records(symbol: str, market: str, df) -> List[Dict]:
        records = []
        for idx, row in df.iterrows():
            try:
                records.append({
                    "symbol": symbol, "market": market,
                    "date": idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10],
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                    "volume": float(row["Volume"]) if row.get("Volume") == row.get("Volume") else 0,
                    "amount": None, "turnover": None,
                })
            except (KeyError, ValueError, TypeError):
                continue
        return records

    def _execute(self, cfg: Dict) -> None:
        market = cfg["market"]
        with self._state_lock:
            self.market = market
        fetch_start = FETCH_START[market]

        # Yahoo 跨 job 互斥：每日增量（data_engine/overseas_daily_updater.py）也在打
        # Yahoo。同时跑 = 两倍请求量，两边都可能被限速。本 job 的单例锁只管住了
        # 「hk/us 不同时跑」（见 :97 的注释），管不到别的 job —— 这里补上。
        #
        # 抢不到就明确停下并报错（形状同下方连通性预检失败：logger.error + status
        # stopped）。每日增量只跑 ~15 分钟，等一会儿重开即可。
        from acquisition.markets.yf_batch import yahoo_job_lock
        with yahoo_job_lock(f"{market} 深历史回补") as ok:
            if not ok:
                logger.error(
                    f"{market} 深历史回补：港美股每日增量正在跑（Yahoo 互斥），本次不启动。"
                    f"它约 15 分钟跑完，稍后重开即可"
                )
                with self._state_lock:
                    self.status = "stopped"
                return
            self._execute_locked(market, fetch_start, cfg)

    def _execute_locked(self, market: str, fetch_start: str, cfg: Dict) -> None:
        todo, already_done, progress = self._resolve_universe(market, cfg)
        confirmed_no_data = set(progress[market]["confirmed_no_data"])

        with self._state_lock:
            self.total_all = len(todo) + already_done
            self.done_total = already_done

        if not todo:
            logger.info(f"{market} 深历史回补：没有需要回补的股票")
            with self._state_lock:
                self.status = "done"
            return

        # 连通性预检：yfinance 网络失败时不抛异常，只是安静返回空 df，容易被
        # 误判成"真的没有数据"从而把 confirmed_no_data 写脏（复现过一次：Clash
        # 用错端口导致全部请求失败，5 只全被误标成永久跳过）。开跑前先拿一只
        # 肯定有数据的锚点票探一下路，连不通就直接中止，不处理任何 symbol。
        anchor = {"hk_stock": "00700.HK", "us_stock": "AAPL"}[market]
        try:
            anchor_df = _call_with_timeout(
                lambda: self._fetch_single(market, anchor, fetch_start),
                SINGLE_FETCH_TIMEOUT, f"{market} 连通性预检",
            )
        except Exception as e:  # noqa: BLE001
            anchor_df = None
            logger.warning(f"{market} 连通性预检异常: {e}")
        if anchor_df is None or anchor_df.empty:
            logger.error(f"{market} 深历史回补：连通性预检失败（{anchor} 拉不到数据），"
                          f"疑似代理/网络问题，本次不处理任何股票，检查网络后重跑")
            with self._state_lock:
                self.status = "stopped"
            return

        logger.info(f"{market} 深历史回补启动：待处理 {len(todo)} 只，已完成 {already_done} 只")

        session = get_session()
        batch_size = cfg["batch_size"]
        sleep_between = cfg["sleep_between_batches"]
        max_retry = cfg["max_retry"]

        try:
            for batch in _chunks(todo, batch_size):
                if self._stop_flag.is_set():
                    logger.info(f"{market} 深历史回补：收到停止信号，处理完当前批次后停下")
                    break

                with self._state_lock:
                    self.current_batch = batch
                    self.last_activity_at = time.time()

                batch_result = None
                for attempt in range(1, max_retry + 1):
                    try:
                        batch_result = _call_with_timeout(
                            lambda: self._fetch_batch(market, batch, fetch_start),
                            BATCH_FETCH_TIMEOUT, f"{market} 批次拉取",
                        )
                        break
                    except Exception as e:  # noqa: BLE001 — yfinance/网络层异常（含超时）统一走退避重试
                        wait = sleep_between * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(f"{market} 批次拉取第 {attempt}/{max_retry} 次失败: {e}，{wait:.1f}s 后重试")
                        with self._state_lock:
                            self.last_activity_at = time.time()
                        time.sleep(wait)

                if batch_result is None:
                    # 整批失败：不确认任何 symbol，留给下次重跑（done_set 天然会跳过已成功的）
                    with self._state_lock:
                        self.failed_batches += 1
                        self.processed_this_run += len(batch)
                    time.sleep(sleep_between)
                    continue

                # 先在本地把整批分类完，不急着提交 confirmed_no_data——
                # 如果一整批一只都没成功，大概率是网络/代理问题而不是真的全部
                # 没数据，那种情况下确认了就永久跳过了，宁可当失败批次重跑
                symbol_results = []  # [(symbol, "ok"|"no_data", df_or_None)]
                for symbol in batch:
                    sub = batch_result.get(symbol)
                    if sub is None or sub.empty:
                        # 批量下载假阴性排查：单只兜底二次确认
                        try:
                            sub2 = _call_with_timeout(
                                lambda: self._fetch_single(market, symbol, fetch_start),
                                SINGLE_FETCH_TIMEOUT, f"{market}:{symbol} 单只兜底",
                            )
                        except Exception:  # noqa: BLE001
                            sub2 = None
                        if sub2 is not None and not sub2.empty:
                            symbol_results.append((symbol, "ok", sub2))
                        else:
                            symbol_results.append((symbol, "no_data", None))
                    else:
                        symbol_results.append((symbol, "ok", sub))

                batch_success_count = sum(1 for _, status, _ in symbol_results if status == "ok")
                if batch_success_count == 0 and len(batch) > 1:
                    logger.warning(f"{market} 批次 {batch[:3]}... 全部无数据，疑似网络问题，当失败批次重跑而不确认")
                    with self._state_lock:
                        self.failed_batches += 1
                        self.processed_this_run += len(batch)
                    time.sleep(sleep_between)
                    continue

                pending_records: List[Dict] = []
                for symbol, status, sub in symbol_results:
                    if status == "no_data":
                        confirmed_no_data.add(symbol)
                        with self._state_lock:
                            self.no_data += 1
                            self.done_total += 1
                            self.processed_this_run += 1
                            self.recent.insert(0, {"symbol": symbol, "status": "no_data",
                                                   "rows": 0, "at": time.strftime("%H:%M:%S")})
                            self.recent = self.recent[:30]
                        continue

                    recs = self._df_to_records(symbol, market, sub)
                    pending_records.extend(recs)
                    with self._state_lock:
                        self.success += 1
                        self.done_total += 1
                        self.processed_this_run += 1
                        self.recent.insert(0, {"symbol": symbol, "status": "ok",
                                               "rows": len(recs), "at": time.strftime("%H:%M:%S")})
                        self.recent = self.recent[:30]

                if pending_records:
                    try:
                        n = bulk_upsert_quotes(session, pending_records)
                        with self._state_lock:
                            self.new_records += n
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"{market} 批量写入失败（丢弃 {len(pending_records)} 条）: {e}")
                        try:
                            session.rollback()
                        except Exception:  # noqa: BLE001
                            pass

                progress[market]["confirmed_no_data"] = list(confirmed_no_data)
                _save_progress(progress)

                time.sleep(sleep_between + random.uniform(0, 1))
        finally:
            session.close()

        progress[market]["confirmed_no_data"] = list(confirmed_no_data)
        _save_progress(progress)

        with self._state_lock:
            self.status = "stopped" if self._stop_flag.is_set() else "done"
            self.current_batch = []
        logger.success(
            f"{market} 深历史回补结束（status={self.status}）：成功 {self.success}, "
            f"无数据 {self.no_data}, 失败批次 {self.failed_batches}, 新记录 {self.new_records}"
        )


# 模块级单例
overseas_deep_history_job = OverseasDeepHistoryJob()
