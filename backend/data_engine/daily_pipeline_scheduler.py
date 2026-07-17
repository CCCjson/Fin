"""
DailyPipelineScheduler — 每日收盘后自动更新数据链（开机自启）。

复刻 knowledge_engine/scheduler.py 的 AsyncIOScheduler 单例骨架。收盘后跑一条链：
    更新全市场日线 (DailyUpdater) → 回补缺失信号 (SignalGenerator) → 更新信号追踪 (SignalTracker)
    → 刷新全市场估值快照 (refresh_all_valuations)

三步都是**同步阻塞**任务（更新日线约几十秒~几分钟），绝不能在 async job 里直接跑，
否则阻塞整个 FastAPI 事件循环。统一用 loop.run_in_executor 丢线程池执行
（仿 knowledge_engine/scheduler.py 与 automation/price_alert_monitor.py）。

门控：环境变量 DAILY_AUTO_UPDATE_ENABLED（默认 true，Jason 要的就是自动跑）。
每一步失败只记 warning，不中断后续，也不影响主服务。运行时可通过 set_enabled()
在页面上动态开关，无需改 .env。

## 启动补跑（2026-07-17 加，别删）

**cron 只在进程活着的那一刻触发。** 本项目的后端跟着桌面 App 起停，15:35 那一刻
后端不在 = 那天的数据**永久丢失，且无声无息**。实测后果：2026-07-09 之后连丢 6 个
交易日（07-10 起全市场只剩 2 只票有数据），没有任何告警。

而且它有**正反馈**：`DailyUpdater` 只有「只差一天」才走快速批量路径，差多天一律
落慢路径逐只抓 —— **断更越久，补得越慢，越难恢复**。

所以 `start()` 会另挂一个一次性 job（延迟 `_CATCHUP_DELAY_SEC`）主动问一句
「今天该有的数据有没有」，缺了就补。**只要哪天开过 App，数据就能自愈。**
"""
import os
import asyncio
import json
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

from loguru import logger


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# 默认配置（可被 .env 覆盖）
_DEFAULT_CRON = os.getenv("DAILY_AUTO_UPDATE_CRON", "").strip()  # 空则用下方 hour/minute
_AUTO_HOUR = int(os.getenv("DAILY_AUTO_UPDATE_HOUR", "15"))
_AUTO_MINUTE = int(os.getenv("DAILY_AUTO_UPDATE_MINUTE", "35"))
JOB_ID = "daily_data_pipeline"
CATCHUP_JOB_ID = "daily_data_pipeline_catchup"

# 启动后隔多久做补跑检查。默认 3 分钟：避开后端启动高峰（约 20-34s 才就绪）+
# 前端首屏抢资源，也让「开关窗抖动」不会连着触发几次（每次都会被 _is_updating 挡，
# 但没必要让它反复进来）。设 0 关闭启动补跑。
_CATCHUP_DELAY_SEC = int(os.getenv("DAILY_AUTO_UPDATE_CATCHUP_DELAY", "180"))

# 覆盖率低于这个百分比就认为「那天整天没跑成」，触发补跑。
#
# 它只负责逮**整天缺失**（实测 07-10 起是 2/5200 = 0.04%，一逮一个准）。
# **不负责逮「跑了但残缺」**：2026-07-09 落了 4216/5200 = 81%，高于此阈值，不会
# 触发补跑 —— 这是**可以接受的**，因为残缺那天漏掉的票会在下次跑时因
# `latest < target_date` 自然落进慢路径补上（`daily_updater` 的三分类逻辑），
# 部分残缺自愈，不需要这里操心。
#
# 取 80 而不是 100：每天总有几十只停牌/退市/新股抓不到，强求 100% 会天天空转。
_CATCHUP_COVERAGE_PCT = float(os.getenv("DAILY_AUTO_UPDATE_CATCHUP_COVERAGE_PCT", "80"))


def _last_expected_trading_day(now: datetime) -> date:
    """粗判「到这会儿为止，最近一个**应该已经有完整数据**的工作日」。

    **刻意不查节假日**：查了要引交易日历，而误判的代价极低 —— 补跑会调
    `DailyUpdater`，它自己会用 `_probe_latest_trading_date()` 探真实交易日；若数据
    其实是全的，全部股票落进 `already_fresh` → `total_need_update == 0` → 立刻
    complete。所以节假日的假阳性 = 一次几秒的空转，换掉一整套交易日历的维护成本。

    宁可多空转一次，不可漏掉一天 —— 漏掉是永久的，空转只是几秒。
    """
    d = now.date()
    # 今天收盘（+落库缓冲）之前，今天本来就还不该有完整数据 → 从昨天开始找
    if (now.hour, now.minute) < (_AUTO_HOUR, _AUTO_MINUTE):
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # 5=周六 6=周日
        d -= timedelta(days=1)
    return d


class DailyPipelineScheduler:
    """每日数据更新链调度器（AsyncIOScheduler 单例）。"""

    def __init__(self):
        self._scheduler = None
        self._running = False
        # 内存开关：默认读 env，运行时可被 set_enabled 覆盖
        self._enabled = _env_bool("DAILY_AUTO_UPDATE_ENABLED", True)
        self._chain_signals = _env_bool("DAILY_AUTO_UPDATE_CHAIN_SIGNALS", True)
        self._chain_tracking = _env_bool("DAILY_AUTO_UPDATE_CHAIN_TRACKING", True)
        self._chain_valuation = _env_bool("DAILY_AUTO_UPDATE_CHAIN_VALUATION", True)
        self._chain_decision_outcome = _env_bool("DAILY_AUTO_UPDATE_CHAIN_DECISION_OUTCOME", True)
        # 最近一次链条运行的结果快照（供前端展示）
        self._last_run: Optional[Dict] = None
        self._is_updating = False  # 防重入（定时 + 手动同时触发）

    # ---------- 状态 ----------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_updating(self) -> bool:
        return self._is_updating

    def _cron_desc(self) -> str:
        return _DEFAULT_CRON if _DEFAULT_CRON else f"{_AUTO_MINUTE} {_AUTO_HOUR} * * 1-5"

    def get_status(self) -> Dict:
        """返回调度器完整状态（供 /data-monitor/overview 聚合）。"""
        jobs = self.get_jobs_info()
        next_run = jobs[0]["next_run"] if jobs else None
        return {
            "running": self._running,
            "enabled": self._enabled,
            "is_updating": self._is_updating,
            "cron": self._cron_desc(),
            # 计划触发时间（供前端/聚合层判「今日是否已过点」；_DEFAULT_CRON 覆盖时为近似值）
            "scheduled_hour": _AUTO_HOUR,
            "scheduled_minute": _AUTO_MINUTE,
            "chain_signals": self._chain_signals,
            "chain_tracking": self._chain_tracking,
            "chain_valuation": self._chain_valuation,
            "chain_decision_outcome": self._chain_decision_outcome,
            "next_run": next_run,
            "jobs": jobs,
            "last_run": self._last_run,
        }

    def get_jobs_info(self) -> List[Dict]:
        if not self._scheduler:
            return []
        return [{"id": j.id, "name": j.name,
                 "next_run": str(j.next_run_time) if j.next_run_time else None}
                for j in self._scheduler.get_jobs()]

    # ---------- 生命周期 ----------

    def _get_scheduler(self):
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            import pytz
            self._scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Shanghai"))
        return self._scheduler

    def _build_trigger(self):
        from apscheduler.triggers.cron import CronTrigger
        if _DEFAULT_CRON:
            return CronTrigger.from_crontab(_DEFAULT_CRON)
        # 收盘后约 5 分钟，仅交易日（周一~周五）
        return CronTrigger(hour=_AUTO_HOUR, minute=_AUTO_MINUTE, day_of_week="mon-fri")

    def start(self):
        """启动调度器。未开启则只启动空调度器（便于运行时再开），不注册 job。"""
        if self._running:
            return
        scheduler = self._get_scheduler()
        if self._enabled:
            self._add_job(scheduler)
            # 补跑检查：cron 兜不住「15:35 那会儿进程没起」，这里兜
            self._add_catchup_job(scheduler)
        scheduler.start()
        self._running = True
        if self._enabled:
            logger.info(f"每日数据更新定时任务已启动（cron={self._cron_desc()}）")
        else:
            logger.info("每日数据更新调度器已启动，但自动更新未开启（DAILY_AUTO_UPDATE_ENABLED=false）")

    def stop(self):
        if not self._running:
            return
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._running = False
        logger.info("每日数据更新定时任务已停止")

    def _add_job(self, scheduler):
        scheduler.add_job(
            self._daily_pipeline_job,
            self._build_trigger(),
            id=JOB_ID,
            name="每日数据更新链",
            replace_existing=True,
            # ⚠️ 这**只**兜「调度器活着、但 job 没能按时跑」（线程池满/调度器暂停）。
            # **它兜不住「进程当时根本没起」** —— 内存 jobstore 下 next_run_time 是
            # add_job 那一刻往后算的，过去的触发点压根不进视野，没有「错过」记录可补。
            # （此处原注释写「服务重启错过触发点，1 小时内仍补跑」，是**假的**，
            #   而且大概率就是「连丢 6 个交易日没人发现」的原因。）
            # 进程没起过的那些天，靠 _catchup_job 补。
            misfire_grace_time=3600,
            coalesce=True,
        )

    def _add_catchup_job(self, scheduler):
        """挂一个一次性的启动补跑检查（延迟执行）。"""
        if _CATCHUP_DELAY_SEC <= 0:
            logger.info("启动补跑已关闭（DAILY_AUTO_UPDATE_CATCHUP_DELAY=0）")
            return
        from apscheduler.triggers.date import DateTrigger
        scheduler.add_job(
            self._catchup_job,
            DateTrigger(run_date=datetime.now() + timedelta(seconds=_CATCHUP_DELAY_SEC)),
            id=CATCHUP_JOB_ID,
            name="启动补跑检查",
            replace_existing=True,
        )

    @staticmethod
    def _coverage_on(day: date) -> tuple:
        """`day` 当天有日线的活跃 A 股占比 -> (有数据只数, 活跃总数)。

        ⚠️ **不能用 `max(DailyQuote.date)` 判新鲜度** —— 全市场 5200 只里只要有 1 只
        领先（真实存在：库里有两只常年比别人快），`max()` 就是最新日期，于是「数据
        很新鲜」，而实际 99.96% 的票都停在一周前。**一个数看着没问题，其实什么都
        没检查** —— 这正是每日链连丢 6 天没人发现的同一类病，别再犯。

        同理必须带 `market == "a_share"` 过滤：不带的话港美股会混进来，
        参见「日线覆盖率 300% bug」那次事故。

        同步查询，调用方负责丢线程池。
        """
        from sqlalchemy import func
        from data_engine.storage.database import get_session
        from data_engine.storage.models import DailyQuote, StockInfo
        session = get_session()
        try:
            total = session.query(func.count(StockInfo.symbol)).filter(
                StockInfo.market == "a_share",
                StockInfo.is_active == 1,
                StockInfo.stock_type != "etf",   # 口径与 DailyUpdater 的 universe 一致
            ).scalar() or 0
            have = session.query(func.count(func.distinct(DailyQuote.symbol))).filter(
                DailyQuote.market == "a_share",
                DailyQuote.date >= day,
            ).scalar() or 0
            return have, total
        finally:
            session.close()

    async def _catchup_job(self):
        """启动补跑：数据落后于最近该有的交易日就补一次链。

        存在的理由见模块 docstring —— cron 只在进程活着时触发，而本项目后端跟着
        桌面 App 起停，没有这个兜底就会**无声无息**地丢掉整天的数据。
        """
        if not self._enabled or self._is_updating:
            return
        try:
            expected = _last_expected_trading_day(datetime.now())
            loop = asyncio.get_event_loop()
            have, total = await loop.run_in_executor(None, self._coverage_on, expected)
            if total <= 0:
                logger.warning("[启动补跑] 活跃 A 股列表为空，跳过（先跑全量导入脚本）")
                return
            pct = have / total * 100
            if pct >= _CATCHUP_COVERAGE_PCT:
                logger.info(
                    f"[启动补跑] {expected} 覆盖率 {pct:.1f}%（{have}/{total}），无需补跑"
                )
                return
            logger.warning(
                f"[启动补跑] {expected} 覆盖率仅 {pct:.1f}%（{have}/{total}）→ 立即补跑每日链。"
                f"cron 只在进程活着时触发，15:35 那会儿后端没起的天数只能靠这里捞回来"
            )
            await self._daily_pipeline_job()
        except Exception as e:  # noqa: BLE001 — 补跑失败绝不能拖垮启动
            logger.exception(f"[启动补跑] 检查异常: {e}")

    def set_enabled(self, enabled: bool) -> Dict:
        """运行时开关自动更新：动态增删 job，无需改 .env / 重启。"""
        self._enabled = enabled
        if self._running and self._scheduler:
            if enabled:
                self._add_job(self._scheduler)
                logger.info(f"自动更新已开启（cron={self._cron_desc()}）")
            else:
                try:
                    self._scheduler.remove_job(JOB_ID)
                except Exception:  # noqa: BLE001 — job 不存在无所谓
                    pass
                logger.info("自动更新已关闭")
        return self.get_status()

    # ---------- 任务本体 ----------

    async def _daily_pipeline_job(self):
        """定时入口：整条链丢线程池串行执行，绝不阻塞事件循环。"""
        if self._is_updating:
            logger.warning("上一次数据更新尚未结束，跳过本次定时触发")
            return
        self._is_updating = True
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._run_pipeline_sync)
            self._last_run = result
            logger.info(f"每日数据更新链完成: {result}")
        except Exception as e:  # noqa: BLE001 — 定时任务绝不抛出
            logger.exception(f"每日数据更新链异常: {e}")
            self._last_run = {"ok": False, "error": str(e)}
        finally:
            self._is_updating = False

    def _run_pipeline_sync(self) -> Dict:
        """同步执行整条链（在线程池里跑）。每步独立 try，失败不中断后续。"""
        from datetime import datetime
        summary: Dict = {"ok": True, "steps": {}, "started_at": datetime.now().isoformat()}

        # 1) 更新全市场日线
        try:
            from data_engine.daily_updater import DailyUpdater
            updater = DailyUpdater()
            complete_event = None
            for chunk in updater.update_stream():
                try:
                    evt = json.loads(chunk)
                    if evt.get("event") == "complete":
                        complete_event = evt
                except (json.JSONDecodeError, TypeError):
                    continue
            summary["steps"]["daily"] = complete_event or {"note": "无 complete 事件"}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[每日链] 日线更新失败: {e}")
            summary["steps"]["daily"] = {"error": str(e)}
            summary["ok"] = False

        # 2) 回补缺失信号
        if self._chain_signals:
            try:
                from strategy.signal_generator import SignalGenerator
                generator = SignalGenerator()
                complete_event = None
                for chunk in generator.backfill_signals_stream(
                    lookback_days=5, save_to_db=True, db_only=True, limit=None
                ):
                    try:
                        evt = json.loads(chunk)
                        if evt.get("event") == "complete":
                            complete_event = evt
                    except (json.JSONDecodeError, TypeError):
                        continue
                summary["steps"]["signals"] = complete_event or {"note": "无缺口或无 complete 事件"}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[每日链] 信号回补失败: {e}")
                summary["steps"]["signals"] = {"error": str(e)}

        # 3) 更新信号追踪
        if self._chain_tracking:
            tracker = None
            try:
                from analysis_engine.signal_tracker import SignalTracker
                tracker = SignalTracker()
                summary["steps"]["tracking"] = tracker.update_all()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[每日链] 信号追踪更新失败: {e}")
                summary["steps"]["tracking"] = {"error": str(e)}
            finally:
                if tracker is not None:
                    tracker.close()

        # 4) 涨停池抓取 + 候选打分（涨停信号预测功能，独立于上面三步，失败不影响主链）
        try:
            from limit_up_engine.service import run_daily_prediction
            summary["steps"]["limit_up"] = run_daily_prediction()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[每日链] 涨停池抓取/打分失败: {e}")
            summary["steps"]["limit_up"] = {"error": str(e)}

        # 5) 刷新全市场估值快照（PE/PB/市值，供选股器 + 数据监控「估值」卡片用）
        if self._chain_valuation:
            try:
                from scripts.backfill_valuation import refresh_all_valuations
                summary["steps"]["valuation"] = refresh_all_valuations()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[每日链] 估值快照刷新失败: {e}")
                summary["steps"]["valuation"] = {"error": str(e)}

        # 6) 回填 AI 建议的后验结果（「上周说买茅台，对了吗」）
        #
        # ⚠️ **必须排在第 1 步「更新日线」之后** —— 回填读 DailyQuote 判建议日之后的
        # 走势，放前面会永远少最新一根 bar。（实测过：库里行情停在 07-09 时，07-10 发的
        # 建议全判 no_quotes；那是**可重试**的 unable，等日线更新完这一步就能评出来。）
        #
        # 不出网：直接 ORM 查 DailyQuote，不走 DataEngine.get_daily_data（那条路在库里
        # 没数据时会自动联网拉）。
        if self._chain_decision_outcome:
            try:
                from decision_log import backfill_outcomes
                summary["steps"]["decision_outcome"] = backfill_outcomes()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[每日链] 决策后验回填失败: {e}")
                summary["steps"]["decision_outcome"] = {"error": str(e)}

        summary["completed_at"] = datetime.now().isoformat()

        # 发业务事件（汇聚到总线，供 MoneyBill 读 / 前端活动流展示）
        try:
            from business_events import publish_event, PIPELINE_DONE
            steps = summary.get("steps", {})
            publish_event(
                PIPELINE_DONE, source="scheduler",
                severity=("warn" if not summary.get("ok") else "info"),
                title=("每日数据更新链完成" if summary.get("ok") else "每日数据更新链完成（有步骤失败）"),
                ok=summary.get("ok"), steps=list(steps.keys()),
            )
        except Exception:  # noqa: BLE001
            pass

        return summary


# 模块级单例
daily_pipeline_scheduler = DailyPipelineScheduler()
