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
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

from loguru import logger

from common.market import A_SHARE
from common.market_time import market_now, market_today


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
OVERSEAS_JOB_ID = "overseas_daily_update"
GAP_JOB_ID = "data_gap_autofill"

# ── 缺口自动补齐（2026-07-27 新增，doc16）─────────────────────────────
#
# `_catchup_job` 管的是**尾部落后**（最近该有的那天没有）。它对**中间的洞**
# 结构性失明：07-10~07-15 全空、07-16 跑了一次，覆盖率就满分了，那 4 个交易日
# 永久是洞而且无声无息。这个 job 专补中间的洞。
#
# 启动后延迟多久跑第一次。默认 420s = 7 分钟，**刻意排在 `_CATCHUP_DELAY_SEC`
# （180s）之后**：尾部补跑可能要跑几分钟，两个都在抢代理池 / Yahoo 锁，串开跑。
_GAP_STARTUP_DELAY_SEC = int(os.getenv("GAP_AUTOFILL_STARTUP_DELAY", "420"))
# 之后每隔多少小时再扫补一次。6 小时：一天四次，够快地发现新洞，又不至于把
# 代理额度耗在反复空扫上（没洞时一次扫描只是几条 SQL，很便宜）。
_GAP_INTERVAL_HOURS = int(os.getenv("GAP_AUTOFILL_INTERVAL_HOURS", "6"))

# ── 港美股：独立 job、独立时点（2026-07-17 Jason 拍板）─────────────────────
# **为什么不并进主链**，两个理由：
# 1. **港股 16:00 HKT 才收盘**，主链 15:35 跑，那会儿拉港股拿到的是没收盘的半截子。
#    （美股无此问题：美东 16:00 收盘 = 北京凌晨 4-5 点，15:35 拿到的是 11h 前已收的
#    完整日线。所以 16:30 对两个市场都成立。）
# 2. 港美股 ~16k 只要跑 10-15 分钟，串在主链里会把后面的信号回补/追踪/涨停预测/
#    估值刷新全部推迟。
_OVERSEAS_HOUR = int(os.getenv("OVERSEAS_AUTO_UPDATE_HOUR", "16"))
_OVERSEAS_MINUTE = int(os.getenv("OVERSEAS_AUTO_UPDATE_MINUTE", "30"))

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

# 信号回补窗口（自然日）。step 2 按「下游标杆落后多少天」自适应算 lookback，钳在
# [MIN, MAX] 之间：
#   - MIN 保留原下限行为（此前硬编码 5）。
#   - MAX 是安全阀：防止 signals 表空/远古时一次触发全历史回填。
#     90 自然日 ≈ 60 交易日，够盖任何现实停机窗口。
# `detect_signal_gaps` 用的是自然日 cutoff（now - timedelta(days=lookback)），故此处单位
# 也是自然日 —— 与它对齐，别混进交易日语义。
_SIGNAL_BACKFILL_MIN_LOOKBACK = int(os.getenv("DAILY_SIGNAL_BACKFILL_MIN_LOOKBACK", "5"))
_SIGNAL_BACKFILL_MAX_LOOKBACK = int(os.getenv("DAILY_SIGNAL_BACKFILL_MAX_LOOKBACK", "90"))

# 港美股启动补跑的门槛：落后**多少天**才补。
#
# ⚠️ **刻意比 A 股钝得多**，因为假阳性代价不是一个量级：
#   - A 股误判 = 几秒空转（DailyUpdater 探到真实交易日，全部 already_fresh 立即 complete）
#   - 港美股误判 = **10-15 分钟白打 Yahoo**（~16k 只全被判为待更新，逐批拉、逐批空手而归）
# 而美股/港股各有独立假期（美股还有夏令时），**没有交易日历就分不清「今天是假期」
# 和「job 没跑」** —— 项目里根本没有交易日历模块。
#
# 取 3：1-2 天落后是常态（周末 / 假期 / 今天 16:30 还没到点），交给 cron 管；
# ≥3 天才说明是真出事了（实测发现时港股落后 9 天、美股 11 天）。
_OVERSEAS_CATCHUP_STALE_DAYS = int(os.getenv("OVERSEAS_CATCHUP_STALE_DAYS", "3"))


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


def _weekday_span(start: date, end: date) -> int:
    """`start`（不含）到 `end`（含）之间的工作日数（周一~周五）。`end <= start` 返回 0。

    用来给港美股补跑判「落后了几个交易日」而非几个自然日 —— 周一早上港/美股上一个
    交易日是上周五，裸算 `(周一 - 周五).days == 3` 会把周末误当落后 3 天、触发一次
    10-15 分钟的空拉。按工作日算，周五→周一只差 1 天，不会误触发。

    **仍不查节假日**（项目无交易日历），所以这只是「周末感知」，不是「交易日历感知」；
    真交易日历式的精确判定超出本次范围，靠阈值的余量吸收假期。
    """
    if end <= start:
        return 0
    n = 0
    d = start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


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
        self._overseas_enabled = _env_bool("OVERSEAS_AUTO_UPDATE_ENABLED", True)
        # 缺口自动补齐（Jason 2026-07-27：「中间有空的天数就自动补齐」）
        self._gap_autofill_enabled = _env_bool("GAP_AUTOFILL_ENABLED", True)
        self._overseas_last_run: Optional[Dict] = None
        self._overseas_updating = False   # 防重入（定时 + 补跑同时触发）
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
            # 港美股是独立 job（独立 cron 16:30），不是链的一步，所以单独一组字段
            "overseas_enabled": self._overseas_enabled,
            "overseas_cron": f"{_OVERSEAS_MINUTE} {_OVERSEAS_HOUR} * * 1-5",
            "overseas_is_updating": self._overseas_updating,
            "overseas_last_run": self._overseas_last_run,
            # 缺口自动补齐（管中间的洞；尾部落后归 catchup 管，两者分工不重叠）
            "gap_autofill_enabled": self._gap_autofill_enabled,
            "gap_autofill_interval_hours": _GAP_INTERVAL_HOURS,
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
        if self._overseas_enabled:
            self._add_overseas_job(scheduler)
        # 补跑检查：cron 兜不住「触发点那会儿进程没起」，这里兜。
        # 只要 A 股/港美股任一开着就要挂 —— 它内部各自判 enabled。
        if self._enabled or self._overseas_enabled:
            self._add_catchup_job(scheduler)
        # 缺口自动补齐：管**中间的洞**，与 catchup（管尾部）分工不重叠
        if self._gap_autofill_enabled:
            self._add_gap_job(scheduler)
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

    def _add_overseas_job(self, scheduler):
        """港美股增量：独立 job、独立 cron（默认 16:30）。理由见文件顶部常量处。"""
        from apscheduler.triggers.cron import CronTrigger
        scheduler.add_job(
            self._overseas_job,
            CronTrigger(hour=_OVERSEAS_HOUR, minute=_OVERSEAS_MINUTE, day_of_week="mon-fri"),
            id=OVERSEAS_JOB_ID,
            name="港美股日线增量",
            replace_existing=True,
            misfire_grace_time=3600,   # 同 JOB_ID：兜不住「进程没起」，那种靠 _catchup_job
            coalesce=True,
        )

    @staticmethod
    def _run_overseas_sync() -> Dict:
        """港美股侧走注册表编排：日历 + 两个市场的日线。

        日历（`calendar.hk_stock` / `calendar.us_stock`）挂在这条链而不是 A 股主链，
        因为它俩要联网拉 ^HSI / ^GSPC，跟港美股日线是同一批网络条件、同一个时点。

        两个市场共抢一次 Yahoo 锁 —— 深历史回补也在打 Yahoo，同时跑等于双倍请求量，
        两边都可能被限速（沿用 `update_overseas_daily` 的既有行为）。
        """
        from acquisition.markets.yf_batch import yahoo_job_lock
        from data_engine.orchestrator import run_assets
        from data_engine.registry import assets_for

        scope = {"calendar.hk_stock", "calendar.us_stock",
                 "daily.hk_stock", "daily.us_stock"}
        keys = [a.key for a in assets_for() if a.key in scope]
        if not keys:
            return {"skipped": "港美股资产已被 env 关闭"}

        with yahoo_job_lock("港美股每日增量") as ok:
            if not ok:
                return {"skipped": "Yahoo 长任务互斥（深历史回补正在跑）"}
            return run_assets(keys, mode="incremental")

    async def _overseas_job(self):
        """港美股增量入口：同步任务丢线程池，绝不阻塞事件循环。"""
        if self._overseas_updating:
            logger.warning("[港美股增量] 上一次尚未结束，跳过本次触发")
            return
        self._overseas_updating = True
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._run_overseas_sync)
            self._overseas_last_run = result
            logger.info(f"[港美股增量] 完成: {result}")
        except Exception as e:  # noqa: BLE001 — 定时任务绝不抛出
            logger.exception(f"[港美股增量] 异常: {e}")
            self._overseas_last_run = {"ok": False, "error": str(e)}
        finally:
            self._overseas_updating = False

    def _add_gap_job(self, scheduler):
        """挂缺口自动补齐：启动后延迟一次 + 之后每 N 小时一次。

        ⛔ **不用 cron**。缺口不是「每天某个时点才会出现」的东西 —— 它是历史遗留，
        什么时候发现什么时候补。用 interval 还能让「开一会儿 App 就关」的用法也
        有机会补上（cron 只在那一刻活着才触发，这正是当初连丢 6 个交易日的机理）。
        """
        from datetime import datetime as _dt

        from apscheduler.triggers.interval import IntervalTrigger
        scheduler.add_job(
            self._gap_job,
            IntervalTrigger(hours=_GAP_INTERVAL_HOURS),
            id=GAP_JOB_ID,
            name="数据缺口自动补齐",
            # 启动即排一次（延迟 _GAP_STARTUP_DELAY_SEC），不等一整个 interval
            next_run_time=_dt.now() + timedelta(seconds=_GAP_STARTUP_DELAY_SEC),
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            f"缺口自动补齐已挂载（{_GAP_STARTUP_DELAY_SEC}s 后首扫，"
            f"之后每 {_GAP_INTERVAL_HOURS} 小时一次）"
        )

    async def _gap_job(self):
        """扫描 + 自动补齐中间缺口。

        真正的活交给 `gap_fill_job`（`BaseSingletonJob`，自带单例线程 + 可停止 +
        进度快照）—— 这里只负责「到点了喊一声」，喊完立刻返回，绝不等它跑完。
        job 自己有防重入（`_launch` 里判线程还活着就拒绝），所以重复触发无害。
        """
        if not self._gap_autofill_enabled:
            return
        try:
            from data_engine.gap_job import gap_fill_job
            result = gap_fill_job.start()
            if result.get("ok"):
                logger.info("[缺口自动补齐] 已启动一轮")
            else:
                logger.debug(f"[缺口自动补齐] 未启动: {result.get('message')}")
        except Exception as e:  # noqa: BLE001 — 定时任务绝不抛出
            logger.exception(f"[缺口自动补齐] 启动异常: {e}")

    def set_gap_autofill_enabled(self, enabled: bool) -> Dict:
        """运行时开关缺口自动补齐。"""
        self._gap_autofill_enabled = enabled
        if self._running and self._scheduler:
            if enabled:
                self._add_gap_job(self._scheduler)
            else:
                try:
                    self._scheduler.remove_job(GAP_JOB_ID)
                except Exception:  # noqa: BLE001
                    pass
                logger.info("缺口自动补齐已关闭")
        return self.get_status()

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

    @staticmethod
    def _overseas_frontier() -> Dict[str, Optional[date]]:
        """港美股各自的「最新行情日期」（前沿）。

        这里用 `max(date)` 是**安全的**，与 A 股那边刻意不用 max 的理由不冲突：
        A 股要判的是「今天这批跑完整没有」（少数领跑者会把 max 拉高、掩盖整体陈旧），
        这里判的是「整个市场是不是被落下好几天了」—— max 恰好是「最乐观的估计」，
        连最乐观的都落后 3 天，那就是真落后了。**宁可漏判不可误判**（误判要白烧
        10-15 分钟 Yahoo 请求）。
        """
        from sqlalchemy import func
        from data_engine.storage.database import get_session
        from data_engine.storage.models import DailyQuote
        session = get_session()
        try:
            out: Dict[str, Optional[date]] = {}
            for mkt in ("hk_stock", "us_stock"):
                d = session.query(func.max(DailyQuote.date)).filter(
                    DailyQuote.market == mkt
                ).scalar()
                if isinstance(d, datetime):
                    d = d.date()
                out[mkt] = d
            return out
        finally:
            session.close()

    @staticmethod
    def _downstream_frontier() -> Optional[date]:
        """下游链「上一次为哪个交易日跑完」的单一标杆 = `max(Signal.date)`。

        **为什么用 signals 当唯一代理**（而不是逐个查信号/追踪/涨停/估值四步）：
          - signals 是日线之后的第一步、最先落后，且**唯一能历史回填**的下游；它落后 =
            整条下游都落后。
          - tracking（`SignalTracker.update_all()` 无日期窗口、处理全部未完成信号）会随
            信号补齐自动跟上，不必单独查。
          - 估值只能从当天续（`refresh_all_valuations` 写 `snapshot_date=today`，历史不可
            回填）、涨停默认只跑今天 —— 这两步不适合当「应回填到哪天」的标杆；但 signals
            触发整条链后它们会顺带刷到当天。

        这里 `func.max` 是**安全的**（与 `_coverage_on` 刻意不用 max 的理由不冲突）：
        signals 是每个交易日**整批全市场**回填的，`max(date)` 反映「该交易日是否已回补」，
        不存在「少数领跑票拉高 max」的问题 —— 与 `_overseas_frontier` 同理。

        **不分市场是对的**：signals 全市场按交易日聚合，不像 `_coverage_on` 那样需要
        `market == "a_share"` 过滤。

        同步查询，调用方负责丢线程池。
        """
        from sqlalchemy import func
        from data_engine.storage.database import get_session
        from data_engine.storage.models import Signal
        session = get_session()
        try:
            d = session.query(func.max(Signal.date)).scalar()
            if isinstance(d, datetime):
                d = d.date()
            return d
        finally:
            session.close()

    @staticmethod
    def _signal_backfill_lookback(sig_latest: Optional[date], today: date) -> int:
        """按下游标杆落后多少天算信号回补窗口（自然日），钳在 [MIN, MAX]。

        写死 5 天的老毛病：停机多日后 5 天窗口够不着老缺口（`detect_signal_gaps` 用的是
        自然日 cutoff）。这里按实际落后自适应，`+3` 是缓冲避免边界日刚好被切掉。
        `sig_latest is None`（表空/异常）→ 用 MAX 兜底，防远古一次触发全历史回填。
        """
        if sig_latest is None:
            return _SIGNAL_BACKFILL_MAX_LOOKBACK
        gap_days = (today - sig_latest).days
        return min(
            max(gap_days + 3, _SIGNAL_BACKFILL_MIN_LOOKBACK),
            _SIGNAL_BACKFILL_MAX_LOOKBACK,
        )

    async def _overseas_catchup(self):
        """港美股启动补跑：落后 >= _OVERSEAS_CATCHUP_STALE_DAYS 个**工作日**才补。

        门槛比 A 股钝得多，理由见 `_OVERSEAS_CATCHUP_STALE_DAYS` 的注释。

        落后天数按**工作日**算（`_weekday_span`），不按自然日：否则周一早上港/美股停在
        上周五会被裸算成「落后 3 天」误触发一次 10-15 分钟的空拉（实测 2026-07-20 就中过）。
        """
        if not self._overseas_enabled or self._overseas_updating:
            return
        try:
            loop = asyncio.get_event_loop()
            frontier = await loop.run_in_executor(None, self._overseas_frontier)
            # 每个市场拿自己时区的今天去算落后天数：拿北京日期问美股会多算一天，
            # 在 _OVERSEAS_CATCHUP_STALE_DAYS=3 的门槛下足以把「正常」误判成「要补跑」，
            # 而港美股误判的代价是 10-15 分钟白打 Yahoo（见文件顶部常量处的说明）
            stale = {
                mkt: _weekday_span(d, market_today(mkt))
                for mkt, d in frontier.items()
                if d and _weekday_span(d, market_today(mkt)) >= _OVERSEAS_CATCHUP_STALE_DAYS
            }
            if not stale:
                logger.info(f"[港美股增量] 启动检查：无需补跑（前沿 {frontier}）")
                return
            logger.warning(
                f"[港美股增量] 启动检查：{stale}（落后工作日数）→ 立即补跑。"
                f"港美股没有别的增量通道，不补就一直不会新"
            )
            await self._overseas_job()
        except Exception as e:  # noqa: BLE001 — 补跑失败绝不能拖垮启动
            logger.exception(f"[港美股增量] 启动检查异常: {e}")

    async def _catchup_job(self):
        """启动补跑：数据落后于最近该有的交易日就补一次链。

        存在的理由见模块 docstring —— cron 只在进程活着时触发，而本项目后端跟着
        桌面 App 起停，没有这个兜底就会**无声无息**地丢掉整天的数据。

        港美股走 `_overseas_catchup`（门槛不同，见那里）。
        """
        await self._overseas_catchup()
        if not self._enabled or self._is_updating:
            return
        try:
            expected = _last_expected_trading_day(market_now(A_SHARE).replace(tzinfo=None))
            loop = asyncio.get_event_loop()
            have, total = await loop.run_in_executor(None, self._coverage_on, expected)
            if total <= 0:
                logger.warning("[启动补跑] 活跃 A 股列表为空，跳过（先跑全量导入脚本）")
                return
            pct = have / total * 100
            daily_short = pct < _CATCHUP_COVERAGE_PCT

            # 下游标杆：日线覆盖率只量得到「日线有没有」，量不到信号/追踪/涨停/估值这些
            # **下游步骤跑没跑**。曾踩过：手动只填了日线把覆盖率顶到 99%，下游却永远
            # 卡在一周前 —— 覆盖率一好就以为全身健康，是「体温计只量一个指标」的病。
            sig_latest = await loop.run_in_executor(None, self._downstream_frontier)
            downstream_stale = (sig_latest is None) or (sig_latest < expected)

            if not daily_short and not downstream_stale:
                logger.info(
                    f"[启动补跑] {expected} 日线覆盖率 {pct:.1f}%（{have}/{total}）、"
                    f"下游标杆 {sig_latest}，均新鲜，无需补跑"
                )
                return
            logger.warning(
                f"[启动补跑] {expected} 日线覆盖率 {pct:.1f}%（{have}/{total}，短缺={daily_short}）、"
                f"下游标杆 {sig_latest}（落后={downstream_stale}）→ 立即补跑每日链。"
                f"cron 只在进程活着时触发，15:35 那会儿后端没起的天数只能靠这里捞回来"
            )
            await self._daily_pipeline_job()
        except Exception as e:  # noqa: BLE001 — 补跑失败绝不能拖垮启动
            logger.exception(f"[启动补跑] 检查异常: {e}")

    def set_enabled(self, enabled: bool) -> Dict:
        """运行时开关 **A 股主链**：动态增删 job，无需改 .env / 重启。

        ⚠️ 这只管 `JOB_ID`（A 股主链）。港美股是独立 job，走 `set_overseas_enabled`。
        """
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

    def set_overseas_enabled(self, enabled: bool) -> Dict:
        """运行时开关**港美股日线** job。

        此前只有 `set_enabled`（管 A 股主链），港美股这个独立 job **在界面上既
        看不到也关不掉** —— `get_status()` 明明返回了 `overseas_enabled` 等字段，
        但没有任何入口能改它，只能改 .env 重启。补上这个方法后
        `/data-monitor/schedulers/overseas_daily/toggle` 才有东西可调。
        """
        self._overseas_enabled = enabled
        if self._running and self._scheduler:
            if enabled:
                self._add_overseas_job(self._scheduler)
                logger.info(f"港美股日线已开启（cron={_OVERSEAS_MINUTE} {_OVERSEAS_HOUR} * * 1-5）")
            else:
                try:
                    self._scheduler.remove_job(OVERSEAS_JOB_ID)
                except Exception:  # noqa: BLE001 — job 不存在无所谓
                    pass
                logger.info("港美股日线已关闭")
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

    def _chain_keys(self) -> List[str]:
        """A 股主链要跑哪些资产 —— 从注册表取，不再硬编码 6 个步骤。

        ⚠️ **不能直接用 `assets_for(cadence="daily_after_close")`**：那会把港美股
        日线也捞进来，而它们是独立 job、独立 cron 16:30（港股 16:00 才收盘，
        15:35 拉到的是没收盘的半截子）。所以这里显式列 A 股侧的 scope。

        `enabled_env` 由注册表各资产自己声明（`DAILY_AUTO_UPDATE_CHAIN_*`），
        `assets_for(only_enabled=True)` 会替我们过滤掉关掉的那些 —— 原来那六个
        `if self._chain_xxx` 判断因此不再需要，开关语义完全一致。
        """
        from data_engine.registry import assets_for, topo_sort
        scope = {
            "calendar.a_share",     # 判缺口的前提，必须在日线之前
            "daily.a_share",
            "financial.a_share",    # 新纳入：此前财报**完全没有自动通道**，只能手点
            "signals",
            "tracking",
            "limit_up.a_share",
            "valuation.a_share",
            "decision_outcome",
        }
        keys = [a.key for a in assets_for() if a.key in scope]
        return topo_sort(keys)

    def _run_pipeline_sync(self) -> Dict:
        """同步执行整条链（在线程池里跑）。

        ## 从「硬编码 6 步」改成「注册表驱动」（2026-07-27）

        原来这里是 6 段几乎逐字重复的 `try / for chunk / json.loads / 找 complete
        事件 / except 记 warning`，每接一个新资产就要再抄一段，而且港美股/crypto/
        新闻这三条链**根本进不来**（它们在别的 job 里）。

        现在编排交给 `orchestrator.run_assets`：拓扑排序、逐个资产跑、单个失败不
        中断后续、每个资产写一行 `DataUpdateLog`、跑完发业务事件 —— 全在那一处。
        本方法只负责「A 股主链该跑哪些资产」这一个决定。

        **行为保持不变的三点**（别在重构里悄悄改掉）：
          1. 每步独立 try，失败只记 warning 不中断后续
          2. `decision_outcome` 必须排在日线之后（靠 `depends_on` 保证）
          3. 跑完发 `PIPELINE_DONE` 业务事件（在 orchestrator 里发）
        """
        from datetime import datetime

        from data_engine.orchestrator import run_assets

        keys = self._chain_keys()
        logger.info(f"[每日链] 本次将跑 {len(keys)} 个资产: {keys}")
        started = datetime.now().isoformat()
        result = run_assets(keys, mode="incremental")

        # 兼容老的返回形状（`steps` 键被 get_status()/前端读着）
        summary: Dict = {
            "ok": result.get("ok", True),
            "steps": result.get("assets", {}),
            "started_at": started,
            "completed_at": result.get("completed_at"),
        }

        # 链跑完顺手扫一遍缺口。**只扫不补** —— 主链已经占着线程池了，补洞可能再
        # 花十几分钟，串在这儿会把 crypto/新闻那两个 interval job 一起拖慢。
        # 真正的补齐由 `_gap_job`（独立触发）负责。
        try:
            from data_engine.storage.database import get_session
            from data_engine.gap_engine import scan_all
            session = get_session()
            try:
                summary["gap_scan"] = scan_all(session)
            finally:
                session.close()
        except Exception as e:  # noqa: BLE001 — 扫描失败不该影响主链结论
            logger.warning(f"[每日链] 缺口扫描失败: {e}")
            summary["gap_scan"] = {"error": str(e)}

        return summary


# 模块级单例
daily_pipeline_scheduler = DailyPipelineScheduler()
