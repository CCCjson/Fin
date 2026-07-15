"""
cninfo 全市场财报批量摄入 —— 常驻后台线程 + 进程内状态，供 API/前端面板直接读。

取代原先的独立脚本 scripts/backfill_knowledge_cninfo.py：Jason 已经有数据监控页
（ScrapeMonitorPanel/KnowledgePanel 那套 polling 面板），批量摄入不该另起一套
独立终端/HTTP server，而是跟它们用同一个模式——后台线程更新进程内状态，
前端轮询 GET 状态接口，起停走 POST。

生命周期骨架（单例线程 + 状态机 + start/stop/snapshot + self-heal）统一由
`data_engine.base_job.BaseSingletonJob` 承接，本类只实现业务体 `_execute` 与
三个状态钩子。断点续传复用同一份进度文件，跟旧脚本共享（不丢已有进度）。
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from data_engine.base_job import BaseSingletonJob
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo
from knowledge_engine.ingest.cninfo_source import ingest_cninfo_filings

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROGRESS_FILE = _BACKEND_DIR / "scripts" / "knowledge_cninfo_progress.json"

_DEFAULT_CATEGORIES = ["年报", "半年报"]
_DEFAULT_SLEEP = 1.0
_DEFAULT_MAX_RETRY = 3
_FLUSH_EVERY = 1  # 每只都刷盘：批次要跑几小时，进度得能实时看


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("done", [])
                data.setdefault("failed", [])
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"进度文件读取失败，重新开始: {e}")
    return {"done": [], "failed": []}


def _save_progress(progress: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    tmp.replace(PROGRESS_FILE)


class CninfoIngestJob(BaseSingletonJob):
    """全市场 cninfo 财报摄入的单例后台任务。"""

    JOB_NAME = "cninfo 批量摄入"
    STOPPING_MSG = "已发送停止信号，会在处理完当前这只后停下"

    def _reset_extra_state(self) -> None:
        self.ingested = 0
        self.skipped = 0
        self.failed_docs = 0
        self.failed_symbols = 0
        self.current_symbol: Optional[str] = None

    # ---------- 对外：起停（snapshot/stop 由基类提供）----------

    def start(
        self,
        *,
        categories: Optional[List[str]] = None,
        per_category: int = 1,
        max_pages: int = 60,
        start_date: str = "20240101",
        end_date: str = "20261231",
        sleep_between: float = _DEFAULT_SLEEP,
        max_retry: int = _DEFAULT_MAX_RETRY,
    ) -> dict:
        return self._launch({
            "categories": categories or _DEFAULT_CATEGORIES,
            "per_category": per_category, "max_pages": max_pages,
            "start_date": start_date, "end_date": end_date,
            "sleep_between": sleep_between, "max_retry": max_retry,
        })

    # ---------- 状态钩子 ----------

    def _remaining(self) -> int:
        return max(0, self.total_all - self.done_total - self.failed_symbols)

    def _clear_current(self) -> None:
        self.current_symbol = None

    def _extra_snapshot_fields(self) -> dict:
        return {
            "failed_symbols": self.failed_symbols,
            "ingested": self.ingested,
            "skipped": self.skipped,
            "failed_docs": self.failed_docs,
            "current_symbol": self.current_symbol,
        }

    # ---------- 业务体（基类 _run 负责异常日志 + finally 兜底翻正）----------

    def _execute(self, cfg: Dict) -> None:
        session = get_session()
        rows = (
            session.query(StockInfo.symbol)
            .filter(StockInfo.stock_type == "stock", StockInfo.is_active == 1,
                    StockInfo.market == "a_share")
            .order_by(StockInfo.symbol)
            .all()
        )
        all_symbols = [r[0] for r in rows]
        session.close()

        progress = _load_progress()
        done_set = set(progress["done"])
        todo = [s for s in all_symbols if s not in done_set]

        with self._state_lock:
            self.total_all = len(all_symbols)
            self.done_total = len(done_set)

        logger.info(
            f"cninfo 批量摄入启动：全市场 {len(all_symbols)} 只，"
            f"已完成 {len(done_set)}，本次待处理 {len(todo)}"
        )

        for i, symbol in enumerate(todo, 1):
            if self._stop_flag.is_set():
                logger.info(f"收到停止信号，在第 {i}/{len(todo)} 只前停下")
                break

            with self._state_lock:
                self.current_symbol = symbol

            result = None
            for attempt in range(1, cfg["max_retry"] + 1):
                try:
                    result = ingest_cninfo_filings(
                        [symbol], categories=cfg["categories"],
                        per_category=cfg["per_category"], max_pages=cfg["max_pages"],
                        start_date=cfg["start_date"], end_date=cfg["end_date"],
                    )
                    break
                except Exception as e:  # noqa: BLE001 — 外部接口异常需兜底重试
                    wait = cfg["sleep_between"] * (2 ** attempt)
                    logger.warning(f"[{i}/{len(todo)}] {symbol} 第 {attempt} 次失败: {e}，{wait:.1f}s 后重试")
                    time.sleep(wait)

            with self._state_lock:
                self.processed_this_run += 1
                self.current_symbol = None
                if result is None:
                    self.failed_symbols += 1
                    if symbol not in progress["failed"]:
                        progress["failed"].append(symbol)
                    self.recent.insert(0, {"symbol": symbol, "status": "failed",
                                           "ingested": 0, "skipped": 0, "failed": 0,
                                           "at": time.strftime("%H:%M:%S")})
                else:
                    self.ingested += result["ingested"]
                    self.skipped += result["skipped"]
                    self.failed_docs += result["failed"]
                    self.done_total += 1
                    progress["done"].append(symbol)
                    if symbol in progress["failed"]:
                        progress["failed"].remove(symbol)
                    self.recent.insert(0, {
                        "symbol": symbol, "status": "ok",
                        "ingested": result["ingested"], "skipped": result["skipped"],
                        "failed": result["failed"], "at": time.strftime("%H:%M:%S"),
                    })
                self.recent = self.recent[:30]

            if i % _FLUSH_EVERY == 0:
                _save_progress(progress)

            time.sleep(cfg["sleep_between"])

        _save_progress(progress)
        with self._state_lock:
            self.status = "stopped" if self._stop_flag.is_set() else "done"
            self.current_symbol = None
        logger.success(f"cninfo 批量摄入结束（status={self.status}）：累计完成 {self.done_total}/{self.total_all}")


# 模块级单例
cninfo_job = CninfoIngestJob()
