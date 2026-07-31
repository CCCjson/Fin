"""「一键更新全部」的后台任务载体。

## 为什么不能走 HTTP 流

现有的手动更新是 NDJSON 流式端点（`POST /data/update-daily/stream`），前端拿
`AbortController` 读，**关掉页面就断**。一键全量要串起 14 个资产、可能跑 30 分钟
以上，走流式意味着：切个页面就中断、刷新一下就看不到进度、后端 generator 被 GC
之后写到一半的资产状态没人收口。

所以改成「后台任务 + 轮询查进度」—— 跟 `DeepHistoryPanel` / `CninfoIngestPanel`
那几个面板已经在用的模式一致（它们是「常驻后台任务，起停 + 轮询」）。

生命周期骨架统一由 `data_engine.base_job.BaseSingletonJob` 承接（单例线程 +
状态机 + 三层 self-heal），本类只实现业务体和状态钩子。

## 进度模型是**两层**的

    总进度：第 3 个资产 / 共 14 个
    子进度：A股日线 1203 / 5201

前端要同时显示这两层，所以 `snapshot()` 里既有 `done_total`（基类的资产级计数），
也有 `asset_progress`（每个资产自己的 current/total）。
"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from data_engine.base_job import BaseSingletonJob


class UpdateAllJob(BaseSingletonJob):
    """一键更新全部数据资产的单例后台任务。"""

    JOB_NAME = "数据全量更新"
    ALREADY_RUNNING_MSG = "已经有一轮更新在跑了"
    STOPPING_MSG = "已发送停止信号，会在当前这个资产跑完后停下（不打断写库）"

    def _reset_extra_state(self) -> None:
        self.mode: str = "incremental"
        self.planned: list[str] = []
        self.current_asset: str | None = None
        # {asset_key: {label, market, status, current, total, updated, failed,
        #              records, fresh_skipped, note}}
        self.asset_progress: dict[str, dict] = {}
        self.results: dict[str, dict] = {}

    def _clear_current(self) -> None:
        self.current_asset = None

    def _extra_snapshot_fields(self) -> dict:
        return {
            "mode": self.mode,
            "planned": list(self.planned),
            "current_asset": self.current_asset,
            "asset_progress": {k: dict(v) for k, v in self.asset_progress.items()},
            "results": {k: dict(v) for k, v in self.results.items()},
        }

    def start(self, *, scope: list[str] | None = None,
              mode: str = "incremental") -> dict:
        """启动一轮更新。

        Args:
            scope: 资产 key 列表；None = 注册表里所有 `in_update_all=True` 的。
            mode: `incremental`（常规增量）或 `gap_fill`（定点补洞）。
        """
        from data_engine.registry import has_asset, topo_sort, update_all_keys

        if scope:
            unknown = [k for k in scope if not has_asset(k)]
            if unknown:
                return {"ok": False, "message": f"未知数据资产: {unknown}", **self.snapshot()}
            keys = topo_sort(list(scope))
        else:
            keys = update_all_keys()
        if not keys:
            return {"ok": False, "message": "没有可更新的资产（都被 env 关掉了？）",
                    **self.snapshot()}
        return self._launch({"keys": keys, "mode": mode})

    def _execute(self, cfg: dict[str, Any]) -> None:
        from data_engine.orchestrator import run_assets
        from data_engine.registry import get_asset

        keys: list[str] = cfg["keys"]
        mode: str = cfg.get("mode", "incremental")

        with self._state_lock:
            self.mode = mode
            self.planned = list(keys)
            self.total_all = len(keys)
            self.done_total = 0
            self.asset_progress = {
                k: {
                    "label": get_asset(k).label,
                    "market": get_asset(k).market,
                    "status": "pending",
                    "current": 0, "total": None,
                    "updated": 0, "failed": 0, "records": 0,
                    "fresh_skipped": 0, "note": "",
                }
                for k in keys
            }

        def _on_event(evt: dict) -> None:
            key = evt.get("asset")
            if not key:
                return
            with self._state_lock:
                slot = self.asset_progress.get(key)
                if slot is None:
                    return
                self.current_asset = key
                kind = evt.get("event")
                if kind == "start":
                    slot["status"] = "running"
                elif kind in ("complete", "error", "skipped"):
                    slot["status"] = {"complete": "done", "error": "failed",
                                      "skipped": "skipped"}[kind]
                else:
                    slot["status"] = "running"
                for f in ("current", "total", "updated", "failed", "records",
                          "fresh_skipped"):
                    if evt.get(f) is not None:
                        slot[f] = evt[f]
                note = evt.get("note") or evt.get("message") or evt.get("symbol")
                if note:
                    slot["note"] = str(note)[:200]

        def _on_done(summary: dict) -> None:
            key = summary.get("asset")
            with self._state_lock:
                self.done_total += 1
                self.processed_this_run += 1
                if key:
                    self.results[key] = summary
                    slot = self.asset_progress.get(key)
                    if slot is not None:
                        slot["status"] = (
                            "done" if summary.get("status") == "success"
                            else "skipped" if summary.get("status") == "skipped"
                            else "partial" if summary.get("status") == "partial"
                            else "failed"
                        )
                    self.recent.insert(0, {
                        "symbol": key,
                        "name": (self.asset_progress.get(key) or {}).get("label", key),
                        "status": "ok" if summary.get("status") in ("success", "skipped")
                                  else "failed",
                        "rows": summary.get("records") or 0,
                        "at": time.strftime("%H:%M:%S"),
                    })
                    self.recent = self.recent[:30]

        result = run_assets(
            keys, mode=mode,
            should_stop=self._stop_flag.is_set,
            on_event=_on_event, on_asset_done=_on_done,
        )

        with self._state_lock:
            self.status = "stopped" if self._stop_flag.is_set() else "done"
            self.current_asset = None
        logger.success(
            f"[全量更新] 收工：{sum(1 for s in result['assets'].values() if s.get('status') == 'success')}"
            f"/{len(result['assets'])} 个资产成功"
        )


# 模块级单例
update_all_job = UpdateAllJob()
