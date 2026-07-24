"""加密货币 7×24 数据链调度器 —— IntervalTrigger 常转，不挂主链、不用工作日 cron。

## 为什么不复用 daily_pipeline 的 cron

股票链是 `CronTrigger(day_of_week="mon-fri")`：只在工作日固定时点跑。crypto 7×24
无休市、无收盘时点，用「工作日某时刻」既漏周末又没意义。改用 `IntervalTrigger`
每 N 分钟常转，增量补最近的 bar + 刷情报。

## 与 FIN_DISABLE_SCHEDULERS 的关系

本调度器由 `api/main.py` startup 拉起，受全局 `FIN_DISABLE_SCHEDULERS` 门控（正常运行不设，
仅调试第二个后端进程时手动关）。自身另有 `CRYPTO_SCHEDULER_ENABLED`（默认 true）细开关。

## 防重入

一轮 `CryptoUpdater.run()` 可能跑几分钟（几百币增量），若上一轮没结束就到下一个
interval，`_updating` 标志位直接跳过本次触发，避免叠跑。
"""
import os
from typing import Any

from loguru import logger

# 每几分钟跑一轮。crypto 日线一天才变一次，30 分钟足够「差一天就补上」还不浪费。
_INTERVAL_MIN = int(os.getenv("CRYPTO_UPDATE_INTERVAL_MIN", "30"))
# 启动后多少秒跑第一轮（补跑，让重启后立刻追平，不用等一个 interval）。
_FIRST_RUN_DELAY = int(os.getenv("CRYPTO_FIRST_RUN_DELAY", "15"))
_ENABLED = os.getenv("CRYPTO_SCHEDULER_ENABLED", "true").lower() != "false"


class CryptoScheduler:
    """crypto 7×24 更新调度器（AsyncIOScheduler 单例）。"""

    def __init__(self):
        self._scheduler: Any = None
        self._updating = False

    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def is_updating(self) -> bool:
        return self._updating

    def get_status(self) -> dict:
        return {
            "is_running": self.is_running(),
            "is_updating": self._updating,
            "enabled": _ENABLED,
            "interval_min": _INTERVAL_MIN,
        }

    def _get_scheduler(self):
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self._scheduler = AsyncIOScheduler(timezone="UTC")
        return self._scheduler

    def start(self):
        if not _ENABLED:
            logger.info("crypto 调度器未启用（CRYPTO_SCHEDULER_ENABLED=false）")
            return
        scheduler = self._get_scheduler()
        if scheduler.running:
            return
        from datetime import datetime, timedelta, timezone

        from apscheduler.triggers.interval import IntervalTrigger
        first_run = datetime.now(timezone.utc) + timedelta(seconds=_FIRST_RUN_DELAY)
        scheduler.add_job(
            self._job,
            IntervalTrigger(minutes=_INTERVAL_MIN),
            id="crypto_update",
            next_run_time=first_run,   # 启动即补跑一轮，不等一个 interval
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.start()
        logger.info(f"crypto 7×24 数据链已启动（每 {_INTERVAL_MIN} 分钟，"
                    f"{_FIRST_RUN_DELAY}s 后首跑）")

    def stop(self):
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("crypto 7×24 数据链已停止")

    async def _job(self):
        """一轮更新（在线程池跑同步 updater，防阻塞事件循环）。防重入。"""
        if self._updating:
            logger.warning("[crypto] 上一轮尚未结束，跳过本次触发")
            return
        self._updating = True
        try:
            import asyncio

            from data_engine.crypto_updater import CryptoUpdater
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, CryptoUpdater().run)
            logger.info(f"[crypto] 更新完成: {result}")
        except Exception as e:  # noqa: BLE001 — job 异常不能掀翻调度器
            logger.exception(f"[crypto] 更新异常: {e}")
        finally:
            self._updating = False

        # 成交明细增量同步（持仓成本的数据源）。**独立于行情更新的成败**：行情挂了
        # 也要同步成交，否则成本停更 → 止损/浮亏风控跟着降级。首轮拉全量，之后按
        # fromId 续拉，很轻。
        try:
            import asyncio

            from crypto_intel_engine import cost_basis as cb
            # `to_thread` 而不是 `get_event_loop().run_in_executor`：域 7 统一过的写法
            # （同步阻塞调用一律 to_thread 包裹），且 get_event_loop 在 3.12 起要弃用
            synced = await asyncio.to_thread(cb.sync_held_fills)
            # ⛔ **总记一行**（哪怕 inserted=0）。此前只在「有新增或有错误」时才记，导致
            # 同步默默失败时日志里一片空白 —— ETH/SOL 成本长期 unknown 却查不到线索，
            # 只能靠「成本显示不出来」才发现。可观测性缺口，宁可多一行 info。
            if synced.get("errors"):
                logger.warning(f"[crypto] 成交明细同步（部分失败）: {synced}")
            else:
                logger.info(f"[crypto] 成交明细同步: {synced}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[crypto] 成交明细同步异常: {e}")


# 模块级单例（与 daily_pipeline_scheduler / news_scheduler 一致的用法）
crypto_scheduler = CryptoScheduler()
