"""
东财研报全市场批量摄入 —— 常驻后台线程 + 进程内状态，供 API/前端面板直接读。

跟 cninfo_job.py 同一套模式：生命周期骨架统一由
`data_engine.base_job.BaseSingletonJob` 承接，本类只实现业务体 `_execute` 与
三个状态钩子 + 进度文件断点续传（todo = all_symbols - done_set）。

2026-07 全量补齐：Jason 拍板全文PDF模式（full_text=True），全市场规模下反爬风险比
小样本验证时更高 —— sleep_between 默认比 cninfo 更保守（1.5s），且 failed_symbols
比例异常升高时应该及时 stop 观察，不要硬跑到底。

2026-07 提速改造：默认改为**两阶段**——全市场主链路默认 full_text=False（仅元数据
要点，快、几乎不吃 CPU），把最重的 PDF 全文解析（fitz）从主循环摘掉；需要全文时
显式传 full_text=True，或跑完元数据后走 POST /ingest/research_report/upgrade_full_text
（upgrade_existing_to_full_text）按需补齐。doc_id 幂等，两阶段互不冲突。
"""
import json
import time
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from data_engine.base_job import BaseSingletonJob
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo
from knowledge_engine.ingest.research_report_source import ingest_research_reports

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROGRESS_FILE = _BACKEND_DIR / "scripts" / "knowledge_research_report_progress.json"

_DEFAULT_SLEEP = 1.5
_DEFAULT_MAX_RETRY = 3
_FLUSH_EVERY = 20  # 每 20 只刷盘一次：整份 progress dict 全量 json.dump，太频繁是纯 IO
                   # 浪费；循环末尾/finally 已有最终刷盘保底，最多丢最近不足 20 只的进度


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


class ResearchReportIngestJob(BaseSingletonJob):
    """全市场东财研报摄入的单例后台任务。"""

    JOB_NAME = "研报批量摄入"
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
        limit_per_symbol: int = 60,
        full_text: bool = False,   # 默认两阶段：主链路只抓元数据，全文按需另开
        start_date: str = "20220101",
        end_date: str = "20261231",
        sleep_between: float = _DEFAULT_SLEEP,
        max_retry: int = _DEFAULT_MAX_RETRY,
    ) -> dict:
        return self._launch({
            "limit_per_symbol": limit_per_symbol, "full_text": full_text,
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
            f"研报批量摄入启动：全市场 {len(all_symbols)} 只，"
            f"已完成 {len(done_set)}，本次待处理 {len(todo)}（full_text={cfg['full_text']}）"
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
                    result = ingest_research_reports(
                        [symbol], start_date=cfg["start_date"], end_date=cfg["end_date"],
                        limit_per_symbol=cfg["limit_per_symbol"], full_text=cfg["full_text"],
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
        logger.success(f"研报批量摄入结束（status={self.status}）：累计完成 {self.done_total}/{self.total_all}")


# 模块级单例
research_report_job = ResearchReportIngestJob()
