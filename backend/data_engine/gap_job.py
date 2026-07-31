"""缺口扫描 + 自动补齐的后台任务。

## 为什么必须是后台 job 而不是同步调用

A 股补一段区间要对 5200 只票逐只发请求（等于一次小型深历史回补，实测十几分钟）。
放在 HTTP 请求里会超时，放在事件循环里会阻塞整个 FastAPI。而且 Jason 要的是
「后端开着就自动补」，那就必须能在后台跑、能看进度、能随时停。

生命周期骨架（单例线程 + 状态机 + start/stop/snapshot + 三层 self-heal）统一由
`data_engine.base_job.BaseSingletonJob` 承接 —— 与深历史/巨潮/研报四个 job 同一套。

⛔ **不用 `ThreadPoolExecutor` 管这个 job 本身**：它的 `__exit__`/atexit 都会 join
卡住的 worker，进程连 SIGTERM 都关不掉（见 memory `deadlock-hang-triage`）。
`BaseSingletonJob` 用的是裸 `threading.Thread(daemon=True)`，正确。
（补洞内部的 A 股多 worker 确实用了 ThreadPoolExecutor，但那是 `shutdown(wait=False)`
且在 finally 里，与被 join 卡死的场景不同 —— 沿用深历史那边验过的写法。）
"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from data_engine.base_job import BaseSingletonJob


class GapFillJob(BaseSingletonJob):
    """缺口扫描 + 自动补齐的单例后台任务。"""

    JOB_NAME = "数据缺口补齐"
    ALREADY_RUNNING_MSG = "缺口补齐已经在跑了"
    STOPPING_MSG = "已发送停止信号，会在当前这个资产处理完后停下"

    def _reset_extra_state(self) -> None:
        self.phase: str = "idle"          # scanning / filling / idle
        self.current_asset: str | None = None
        self.scan_result: dict | None = None
        self.fills: list[dict] = []
        self.gaps_found: int = 0
        self.gaps_fillable: int = 0
        self.days_filled: int = 0
        self.days_permanent: int = 0

    def _clear_current(self) -> None:
        self.current_asset = None
        self.phase = "idle"

    def _extra_snapshot_fields(self) -> dict:
        return {
            "phase": self.phase,
            "current_asset": self.current_asset,
            "gaps_found": self.gaps_found,
            "gaps_fillable": self.gaps_fillable,
            "days_filled": self.days_filled,
            "days_permanent": self.days_permanent,
            "scan_result": self.scan_result,
            "fills": list(self.fills),
        }

    def start(self, *, scan_only: bool = False, asset_key: str | None = None,
              lookback_days: int | None = None) -> dict:
        """启动一轮扫描（+ 补齐）。

        Args:
            scan_only: 只扫不补 —— 想先看看缺哪些天再决定要不要补时用。
            asset_key: 只处理这一个资产（None = 全部支持补洞的）。
            lookback_days: 覆盖默认扫描窗口。
        """
        return self._launch({
            "scan_only": scan_only,
            "asset_key": asset_key,
            "lookback_days": lookback_days,
        })

    def _execute(self, cfg: dict[str, Any]) -> None:
        from data_engine.gap_engine import fill_asset_gaps, fillable_gaps, scan_all
        from data_engine.storage.database import get_session

        # ---- 阶段 1：扫描 ----
        with self._state_lock:
            self.phase = "scanning"

        session = get_session()
        try:
            scan = scan_all(session, lookback_days=cfg.get("lookback_days"))
            todo = fillable_gaps(session, asset_key=cfg.get("asset_key"))
        finally:
            session.close()

        with self._state_lock:
            self.scan_result = scan
            self.gaps_found = scan.get("total_gaps", 0)
            self.gaps_fillable = scan.get("fillable", 0)
            # total_all/done_total 是基类算 ETA 用的公共字段，这里以「要补的天数」计
            self.total_all = sum(len(v) for v in todo.values())
            self.done_total = 0

        if cfg.get("scan_only"):
            logger.info(f"[缺口补齐] 只扫不补：发现 {self.gaps_found} 个缺口，"
                        f"其中 {self.gaps_fillable} 个可自动补")
            with self._state_lock:
                self.status = "done"
            return

        if not todo:
            logger.info("[缺口补齐] 没有可自动补的缺口，收工")
            with self._state_lock:
                self.status = "done"
            return

        # ---- 阶段 2：补齐 ----
        with self._state_lock:
            self.phase = "filling"

        logger.warning(
            f"[缺口补齐] 开始补 {self.total_all} 个缺口日，涉及资产 {list(todo)}"
        )
        for asset_key, days in todo.items():
            if self._stop_flag.is_set():
                logger.info("[缺口补齐] 收到停止信号，剩余资产不再处理")
                break
            with self._state_lock:
                self.current_asset = asset_key
            # ⛔ **一个资产炸了不能掀翻整个 job**。实测踩到：补涨停池时
            # `limit_up` 的 ingest 持着长写事务，`_mark` 的 commit 撞
            # `database is locked` 抛出来，整个 job 当场死掉、7 天一个没补成，
            # 前端只看到 status=stopped、计数全 0 —— 比「补失败」更糟，因为
            # 看不出发生了什么。（commit 本身现已走 `commit_with_retry` 退避，
            # 这里是第二道防线：任何异常都不该让后面的资产陪葬。）
            try:
                result = fill_asset_gaps(
                    asset_key, days, should_stop=self._stop_flag.is_set,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[缺口补齐] {asset_key} 异常: {e}")
                result = {"asset": asset_key, "error": str(e),
                          "requested": [d.isoformat() for d in days]}
            with self._state_lock:
                self.fills.append(result)
                self.processed_this_run += len(days)
                self.done_total += len(days)
                self.days_filled += len(result.get("filled") or [])
                self.days_permanent += len(result.get("marked_permanent") or [])
                self.recent.insert(0, {
                    "symbol": asset_key,
                    "name": asset_key,
                    "status": "ok" if not result.get("error") else "failed",
                    "rows": len(result.get("filled") or []),
                    "at": time.strftime("%H:%M:%S"),
                })
                self.recent = self.recent[:30]

        with self._state_lock:
            self.status = "stopped" if self._stop_flag.is_set() else "done"
            self.current_asset = None
            self.phase = "idle"
        logger.success(
            f"[缺口补齐] 完成：补上 {self.days_filled} 天，"
            f"认定假期 {self.days_permanent} 天"
        )


# 模块级单例（同 deep_history / cninfo / research_report 四个 job 的用法）
gap_fill_job = GapFillJob()
