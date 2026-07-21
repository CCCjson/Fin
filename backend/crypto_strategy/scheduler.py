"""crypto 自主策略引擎 7×24 调度器 —— 每 N 分钟 tick 一次，跑 CryptoStrategyEngine（需求3）。

镜像 `data_engine/crypto_scheduler.py`：`IntervalTrigger` 常转（7×24 无 cron）、`max_instances=1`
+ `_running` 防重入、`run_in_executor` 卸载同步引擎、启动延时补跑一轮。

⛔ 安全默认 `CRYPTO_STRATEGY_ENGINE_ENABLED=false`（与数据调度器默认 true 相反）——自主下单
不能靠默认拉起，必须显式开。仍受全局 `FIN_DISABLE_SCHEDULERS` 门控。引擎内部还有 kill-switch。
每策略节奏由各自 `interval_minutes` 决定，引擎按 last_run_at 判到期，故本调度器只需高频 tick。
"""
import os
from typing import Any

from loguru import logger

# 引擎 tick 间隔（分钟）。取 universe 里最短策略节奏的下限即可；默认 5 分钟够短线用。
_TICK_MIN = int(os.getenv("CRYPTO_STRATEGY_TICK_MIN", "5"))
_FIRST_RUN_DELAY = int(os.getenv("CRYPTO_STRATEGY_FIRST_RUN_DELAY", "20"))
# ⛔ 安全默认关：自主实盘下单不靠默认开启
_ENABLED = os.getenv("CRYPTO_STRATEGY_ENGINE_ENABLED", "false").lower() in ("1", "true", "yes", "on")


class CryptoStrategyScheduler:
    def __init__(self):
        self._scheduler: Any = None
        self._running = False

    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def get_status(self) -> dict:
        from crypto_strategy import guardrails as gr
        return {
            "is_running": self.is_running(),
            "ticking": self._running,
            "enabled": _ENABLED,
            "killed": gr.is_killed(),
            "tick_min": _TICK_MIN,
        }

    def _get_scheduler(self):
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self._scheduler = AsyncIOScheduler(timezone="UTC")
        return self._scheduler

    def start(self, force: bool = False):
        """force=False：受 env 门控（开机自启用，默认关）；force=True：手动点按钮，无视 env 强制起。

        env `CRYPTO_STRATEGY_ENGINE_ENABLED` 只该管「开机要不要自动跑」，不该挡用户手动启动——
        半自动引擎起来也只是排待确认单（需逐笔确认），手动开是安全的。
        """
        if not _ENABLED and not force:
            logger.info("crypto 策略引擎未开机自启（CRYPTO_STRATEGY_ENGINE_ENABLED=false）；可手动启动")
            return
        scheduler = self._get_scheduler()
        if scheduler.running:
            return
        from datetime import datetime, timedelta, timezone

        from apscheduler.triggers.interval import IntervalTrigger
        first_run = datetime.now(timezone.utc) + timedelta(seconds=_FIRST_RUN_DELAY)
        scheduler.add_job(
            self._job,
            IntervalTrigger(minutes=_TICK_MIN),
            id="crypto_strategy_tick",
            next_run_time=first_run,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.start()
        logger.info(f"crypto 自主策略引擎已启动（每 {_TICK_MIN} 分钟 tick，{_FIRST_RUN_DELAY}s 后首跑）")

    def stop(self):
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("crypto 自主策略引擎已停止")

    async def _job(self):
        if self._running:
            logger.warning("[crypto策略] 上一 tick 未结束，跳过")
            return
        self._running = True
        try:
            import asyncio

            from crypto_strategy.engine import crypto_strategy_engine
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, crypto_strategy_engine.run)
            logger.info(f"[crypto策略] tick 完成: {result}")
        except Exception as e:  # noqa: BLE001 — tick 异常不掀翻调度器
            logger.exception(f"[crypto策略] tick 异常: {e}")
        finally:
            self._running = False


crypto_strategy_scheduler = CryptoStrategyScheduler()
