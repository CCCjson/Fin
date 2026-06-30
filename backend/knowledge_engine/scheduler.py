"""
KnowledgeScheduler — 定期摄入 arxiv 论文（只摄入，绝不回测）。

复刻 automation/scheduler.py 的 AsyncIOScheduler 单例骨架。慢任务（DDG 8s/次 +
下载 + 本地 embedding）用 run_in_executor 丢线程池，绝不在 async job 里直接调，
否则阻塞整个 FastAPI 事件循环（仿 automation/price_alert_monitor.py）。

双重门控：仅当 KNOWLEDGE_SCHED_INGEST_ENABLED=true 且 KNOWLEDGE_SEARCH_QUERIES 非空才注册 job。
"""
import asyncio
from typing import List, Dict

from loguru import logger

from knowledge_engine.config import (
    get_sched_ingest_enabled, get_ingest_cron, get_sched_max_docs, get_search_queries,
)


class KnowledgeScheduler:
    """知识库定时摄入调度器（AsyncIOScheduler 单例）。"""

    def __init__(self):
        self._scheduler = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _get_scheduler(self):
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            import pytz
            self._scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Shanghai"))
        return self._scheduler

    def start(self):
        """启动调度器。双重门控：未开启 / 无 query 则 no-op。"""
        if self._running:
            return
        if not get_sched_ingest_enabled():
            logger.info("知识库定时摄入未开启（KNOWLEDGE_SCHED_INGEST_ENABLED=false），跳过")
            return
        if not get_search_queries():
            logger.warning("知识库定时摄入已开启但 KNOWLEDGE_SEARCH_QUERIES 为空，不注册任务")
            return

        from apscheduler.triggers.cron import CronTrigger
        scheduler = self._get_scheduler()
        scheduler.add_job(
            self._ingest_job,
            CronTrigger.from_crontab(get_ingest_cron()),
            id="knowledge_ingest",
            name="定时摄入论文",
            replace_existing=True,
        )
        scheduler.start()
        self._running = True
        logger.info(f"知识库定时摄入已启动（cron={get_ingest_cron()}）")

    def stop(self):
        if not self._running:
            return
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._running = False
        logger.info("知识库定时摄入已停止")

    def get_jobs_info(self) -> List[Dict]:
        if not self._scheduler:
            return []
        return [{"id": j.id, "name": j.name,
                 "next_run": str(j.next_run_time) if j.next_run_time else None}
                for j in self._scheduler.get_jobs()]

    async def _ingest_job(self):
        """定时摄入：慢任务丢线程池，绝不阻塞事件循环。只摄入，不回测。"""
        from knowledge_engine.ingest.web_source import ingest_papers
        queries = get_search_queries()
        if not queries:
            return
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: ingest_papers(queries, max_docs=get_sched_max_docs())
            )
            logger.info(f"定时摄入完成: {result}")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"定时摄入失败: {e}")


# 模块级单例
knowledge_scheduler = KnowledgeScheduler()
