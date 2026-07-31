"""数据资产注册表 —— 一张表驱动四件事。

## 为什么要有它（2026-07-27）

此前「定时调度、增量判定、事件格式、界面卡片」四件事各写各的：

- `daily_pipeline_scheduler._run_pipeline_sync()` 里硬编码 6 个步骤
- 港美股是另一个 job、crypto 是另一个 scheduler、新闻是第三个
- 前端 `DataMonitor.tsx` 里 7 张卡各自 hardcode 一个 service 调用
- 新接一个资产要在四个地方各改一遍，漏一处就是「界面上看不见的定时任务」
  （港美股 job 至今在前端不可见不可关，就是这么来的）

注册表把「有哪些数据资产、各自怎么更新」收成**唯一真源**，然后：

    DataAsset ──┬──> 定时调度（按 cadence 遍历挂 job）
                ├──> 一键全量（按 depends_on 拓扑排序依次跑）
                ├──> 缺口扫描（有 gap_probe 的资产逐个扫）
                └──> 前端资产矩阵（直接映射成格子）

## runner 契约

每个 runner 是 `(RunContext) -> Iterator[dict]`，yield 的 dict 必须符合
`data_engine/events.py` 的契约。老 updater 的输出用 `events.normalize_stream`
翻译，**刻意不改它们内部** —— 三个 updater 各带着几十条踩坑注释和实测边界条件
（盘中脏数据判定、frontier skip、代理熔断……），为改字段名去动它们，回归风险
远大于收益。

## 为什么深历史/知识库不进「一键全量」

`in_update_all=False` 的那几个是**小时级**任务，而且深历史和港美股日线**抢同一把
Yahoo 锁**（`yf_batch.yahoo_job_lock`）会互相阻塞。混进「一键更新全部」会让人
点完卡半天。它们仍然登记在册（矩阵里能看到状态、能单独触发），只是不参与全量。
（2026-07-27 Jason 确认）
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from loguru import logger

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK
from data_engine.events import make_event, normalize_stream

Cadence = Literal["daily_after_close", "interval", "on_demand"]
Group = Literal["calendar", "quote", "fundamental", "sentiment", "derived"]


@dataclass
class RunContext:
    """一次 runner 调用的上下文。"""

    # incremental = 常规增量（只补落后的）；gap_fill = 定点补指定日期
    mode: Literal["incremental", "gap_fill"] = "incremental"
    # gap_fill 模式下要补的日期（升序）。incremental 模式忽略。
    gap_dates: tuple[date, ...] = ()
    # 只处理前 N 个标的（调试用）
    limit: int | None = None
    # 覆盖「今天」（测试用）
    today: date | None = None
    # 协作式停止信号：runner 在每个自然断点检查它，为真就收尾退出。
    # ⛔ 不用强杀 —— 写库写到一半被杀会留下半截事务。
    should_stop: Callable[[], bool] = field(default=lambda: False)


@dataclass(frozen=True)
class DataAsset:
    """一个「资产 × 市场」的更新单元。"""

    key: str                       # "daily.a_share"，全局唯一
    label: str                     # "A股日线"
    group: Group
    market: str | None             # canonical market；跨市场资产为 None
    cadence: Cadence
    runner: Callable[[RunContext], Iterator[dict]]

    # 拓扑排序用：这个资产依赖哪些资产先跑完
    depends_on: tuple[str, ...] = ()
    # 是否纳入「一键更新全部」
    in_update_all: bool = True
    # 是否支持定点补某一天（有 gap_probe 才可能为 True）
    supports_gap_fill: bool = False
    # 缺口探测：`(session, start, end) -> {日期: 该日实际条数}`。
    # gap_engine 拿它和交易日历做差集。None = 不参与缺口扫描。
    gap_probe: Callable[[Any, date, date], dict[date, int]] | None = None
    # 判「这天算缺」的口径。**选错会疯狂误报**，见 gap_engine._threshold_for：
    #   coverage —— 当日条数 < 近期中位数 × 0.8（日线类，每天该有的票数稳定）
    #   presence —— 只要 > 0 就算有（涨停池类，家数本身天天剧烈波动）
    gap_mode: Literal["coverage", "presence"] = "coverage"
    # 状态探测：`(session) -> {"latest_date","count","detail"}`，供资产矩阵展示。
    # 与 `gap_probe` 分开：gap_probe 是「逐日的条数」（判缺口用），status_probe 是
    # 「这个资产整体现在什么样」。没有 gap_probe 的资产（财报/估值/新闻/行业）
    # 只有后者 —— 缺了它矩阵里就是一格「unknown」，等于没做。
    status_probe: Callable[[Any], dict] | None = None
    # 运行时门控的环境变量名（None = 恒开）
    enabled_env: str | None = None
    # 一句话说明，给前端 tooltip
    hint: str = ""

    def enabled(self) -> bool:
        """env 门控。**每次调用都重读 os.getenv** —— 不缓存是刻意的：
        `.env` 改完重启后端才生效，但测试要能 monkeypatch 环境变量。"""
        if not self.enabled_env:
            return True
        val = os.getenv(self.enabled_env)
        if val is None:
            return True
        return val.strip().lower() in ("1", "true", "yes", "on")


# ====================================================================
# runner 适配器
# ====================================================================


def _stream_runner(asset_key: str, market: str | None,
                   make_stream: Callable[[RunContext], Iterator[str]]):
    """包装一个 yield NDJSON 行的老 updater。"""
    def runner(ctx: RunContext) -> Iterator[dict]:
        yield from normalize_stream(make_stream(ctx), asset_key, market)
    return runner


def _dict_runner(asset_key: str, market: str | None,
                 call: Callable[[RunContext], dict],
                 *, field_map: dict[str, str] | None = None):
    """包装一个「跑完返回一个 dict」的同步函数 → start + complete 两个事件。

    `field_map` 把返回 dict 里的键翻译成契约字段，例如
    `{"saved": "records", "total": "updated"}`。没映射到的键原样带上（前端要看细节）。

    ⛔ **透传时必须先剥掉 `event`/`asset`/`market`**。这三个键由 `make_event` 以
    位置参数给出，被调函数的返回 dict 里若也有同名键，`**payload` 就会撞成
    `make_event() got multiple values for argument 'market'`。

    实测炸过：`refresh_calendar()` 返回 `{"market", "added", "source", "latest"}`，
    于是**三个 calendar 资产每次定时跑都当场失败**，连着三天没人发现 —— 因为
    编排器「单资产失败只记 warning 不中断」，日志里只是一行 note，界面上那格
    还因为 `_probe_latest` 读的是表而不是本次运行结果，照样显示绿的。
    （2026-07-31 由「后台自己跑了几天」暴露出来。）
    """
    reserved = ("event", "asset", "market")

    def runner(ctx: RunContext) -> Iterator[dict]:
        yield make_event("start", asset_key, market, total=None)
        result = call(ctx) or {}
        payload: dict[str, Any] = {}
        for src, dst in (field_map or {}).items():
            if result.get(src) is not None:
                payload[dst] = result[src]
        for k, v in result.items():
            if k in reserved or k in (field_map or {}):
                continue
            payload.setdefault(k, v)
        if result.get("error"):
            yield make_event("error", asset_key, market, message=str(result["error"]))
        else:
            yield make_event("complete", asset_key, market, **payload)
    return runner


# ====================================================================
# 各资产的具体 runner
# ====================================================================


def _run_calendar(market: str):
    def call(ctx: RunContext) -> dict:
        from data_engine.storage.database import get_session
        from data_engine.trading_calendar import refresh_calendar
        session = get_session()
        try:
            return refresh_calendar(session, market, today=ctx.today)
        finally:
            session.close()
    return _dict_runner(f"calendar.{market}", market, call,
                        field_map={"added": "records"})


def _run_daily_a_share(ctx: RunContext) -> Iterator[dict]:
    """A 股日线。incremental 走 `DailyUpdater.update_stream`；gap_fill 走定点补。"""
    if ctx.mode == "gap_fill":
        from data_engine.gap_fill import fill_a_share_days
        yield from fill_a_share_days(ctx)
        return
    from data_engine.daily_updater import DailyUpdater
    yield from normalize_stream(DailyUpdater().update_stream(), "daily.a_share", A_SHARE)


def _run_daily_overseas(market: str):
    def runner(ctx: RunContext) -> Iterator[dict]:
        if ctx.mode == "gap_fill":
            from data_engine.gap_fill import fill_overseas_days
            yield from fill_overseas_days(market, ctx)
            return
        from data_engine.overseas_daily_updater import OverseasDailyUpdater
        stream = OverseasDailyUpdater().run_stream(market, today=ctx.today, limit=ctx.limit)
        yield from normalize_stream(stream, f"daily.{market}", market)
    return runner


def _run_crypto_kline(ctx: RunContext) -> Iterator[dict]:
    from data_engine.crypto_updater import CryptoUpdater
    from data_engine.storage.database import get_session

    key = "kline.crypto"
    yield make_event("start", key, CRYPTO)
    session = get_session()
    try:
        updater = CryptoUpdater()
        if ctx.mode == "gap_fill" and ctx.gap_dates:
            # crypto 的补洞就是加长回看窗口 —— 币安 klines 一次拉一段，
            # `backfill_klines` 内部 upsert，重叠区间不会重复落行
            anchor = ctx.today or max(ctx.gap_dates)
            span = (anchor - min(ctx.gap_dates)).days
            result = updater.backfill_klines(session, lookback_days=max(span + 5, 30))
        else:
            result = updater.update_klines(session)
        yield make_event("complete", key, CRYPTO,
                         updated=result.get("updated") or result.get("symbols") or 0,
                         records=result.get("rows") or result.get("inserted") or 0,
                         **{k: v for k, v in (result or {}).items()
                            if k not in ("updated", "symbols", "rows", "inserted")})
    except Exception as e:  # noqa: BLE001 — 单个资产失败不该掀翻整条链
        logger.warning(f"[registry] kline.crypto 失败: {e}")
        yield make_event("error", key, CRYPTO, message=str(e))
    finally:
        session.close()


def _run_financial(ctx: RunContext) -> Iterator[dict]:
    from data_engine.financial_updater import FinancialUpdater
    stream = FinancialUpdater().update_stream(mode="incremental")
    yield from normalize_stream(stream, "financial.a_share", A_SHARE)


def _run_signals(ctx: RunContext) -> Iterator[dict]:
    """信号回补。lookback 按下游标杆实际落后多少天自适应（沿用主链既有逻辑）。"""
    from common.market_time import market_today
    from data_engine.daily_pipeline_scheduler import DailyPipelineScheduler
    from strategy.signal_generator import SignalGenerator

    lookback = DailyPipelineScheduler._signal_backfill_lookback(
        DailyPipelineScheduler._downstream_frontier(),
        ctx.today or market_today(A_SHARE),
    )
    stream = SignalGenerator().backfill_signals_stream(
        lookback_days=lookback, save_to_db=True, db_only=True, limit=None,
    )
    yield from normalize_stream(stream, "signals", None)


def _run_tracking(ctx: RunContext) -> dict:
    from analysis_engine.signal_tracker import SignalTracker
    tracker = SignalTracker()
    try:
        return tracker.update_all()
    finally:
        tracker.close()


def _run_limit_up(ctx: RunContext) -> dict:
    from limit_up_engine.service import run_daily_prediction
    if ctx.mode == "gap_fill" and ctx.gap_dates:
        out: dict = {"filled_dates": []}
        for d in ctx.gap_dates:
            if ctx.should_stop():
                break
            try:
                run_daily_prediction(d.strftime("%Y%m%d"))
                out["filled_dates"].append(d.isoformat())
            except Exception as e:  # noqa: BLE001 — 单天失败不中断其余天
                logger.warning(f"[registry] limit_up 补 {d} 失败: {e}")
        return out
    return run_daily_prediction()


def _run_valuation(ctx: RunContext) -> dict:
    # ⚠️ 估值**只能从当天续**：`refresh_all_valuations` 写 `snapshot_date=今天`，
    # 历史快照拉不回来（见 memory portfolio-module-plan「估值只有 8 天快照不可用」）。
    # 所以它 supports_gap_fill=False —— 缺的历史估值是永久缺的，报缺口只会天天刷屏。
    from scripts.backfill_valuation import refresh_all_valuations
    return refresh_all_valuations()


def _run_industry(ctx: RunContext) -> dict:
    from data_engine.industry_updater import backfill_industry
    return backfill_industry(resume=True)


def _run_news(ctx: RunContext) -> dict:
    from news_engine.news_scheduler import news_scheduler
    return news_scheduler.run_once_sync()


def _run_decision_outcome(ctx: RunContext) -> dict:
    # ⚠️ 必须排在日线之后 —— 回填读 DailyQuote 判建议日之后的走势，
    # 放前面会永远少最新一根 bar（depends_on 保证了顺序）
    from decision_log import backfill_outcomes
    return backfill_outcomes()


def _run_realtime(ctx: RunContext) -> dict:
    from acquisition.markets.realtime import fetch_a_share_realtime_cached
    rows = fetch_a_share_realtime_cached(force=True)
    return {"updated": len(rows or []), "records": len(rows or [])}


# ====================================================================
# 缺口探测器
# ====================================================================


def _probe_daily(market: str):
    """日线资产的缺口探测：每个日期有多少只票有 bar。

    ⛔ **必须带 market 过滤** —— 漏了就是「日线覆盖率 300% bug」（港美股的行混进
    A 股的分子，实测 19871/6594 = 301.3%），见 docs/GOTCHAS.md。
    """
    def probe(session, start: date, end: date) -> dict[date, int]:
        from sqlalchemy import func

        from data_engine.storage.models import DailyQuote
        from data_engine.trading_calendar import _to_date
        rows = (session.query(DailyQuote.date, func.count(func.distinct(DailyQuote.symbol)))
                .filter(DailyQuote.market == market,
                        DailyQuote.date >= start,
                        DailyQuote.date <= end)
                .group_by(DailyQuote.date).all())
        out: dict[date, int] = {}
        for raw_day, count in rows:
            d = _to_date(raw_day)
            if d:
                out[d] = int(count)
        return out
    return probe


# ====================================================================
# 状态探测器（资产矩阵用）
#
# 契约：`(session) -> {"latest_date": date|None, "count": int|None, "detail": str}`
# `detail` 是给前端格子第二行显示的一句话，各资产口径不同，这里各写各的。
# ====================================================================


def _status_calendar(market: str):
    def probe(session) -> dict:
        from sqlalchemy import func

        from data_engine.storage.models import TradingCalendar
        from data_engine.trading_calendar import BENCHMARKS, _to_date
        row = session.query(
            func.count(TradingCalendar.id), func.max(TradingCalendar.cal_date),
        ).filter(TradingCalendar.market == market).first()
        count, latest = row if row else (0, None)
        return {
            "latest_date": _to_date(latest),
            "count": int(count or 0),
            "detail": f"{int(count or 0)} 个交易日 · 基准 {BENCHMARKS.get(market)}",
        }
    return probe


def _status_financial(session) -> dict:
    from sqlalchemy import func

    from data_engine.storage.models import FinancialData, StockInfo
    from data_engine.trading_calendar import _to_date
    total = session.query(func.count(StockInfo.symbol)).filter(
        StockInfo.market == A_SHARE, StockInfo.stock_type == "stock",
        StockInfo.is_active == 1,
    ).scalar() or 0
    have = session.query(func.count(func.distinct(FinancialData.symbol))).scalar() or 0
    latest = session.query(func.max(FinancialData.report_date)).scalar()
    pct = round(have / total * 100, 1) if total else 0
    return {"latest_date": _to_date(latest), "count": int(have),
            "detail": f"覆盖 {pct}%（{have}/{total} 只）"}


def _status_valuation(session) -> dict:
    from sqlalchemy import func

    from data_engine.storage.models import StockValuation
    from data_engine.trading_calendar import _to_date
    latest = _to_date(session.query(func.max(StockValuation.snapshot_date)).scalar())
    count = 0
    if latest:
        count = session.query(func.count(StockValuation.id)).filter(
            StockValuation.snapshot_date == latest,
        ).scalar() or 0
    return {"latest_date": latest, "count": int(count),
            "detail": f"{count} 只有估值快照"}


def _status_industry(session) -> dict:
    from sqlalchemy import func

    from data_engine.storage.models import StockInfo
    total = session.query(func.count(StockInfo.symbol)).filter(
        StockInfo.market == A_SHARE, StockInfo.stock_type == "stock",
        StockInfo.is_active == 1,
    ).scalar() or 0
    have = session.query(func.count(StockInfo.symbol)).filter(
        StockInfo.market == A_SHARE, StockInfo.stock_type == "stock",
        StockInfo.is_active == 1, StockInfo.industry.isnot(None),
    ).scalar() or 0
    pct = round(have / total * 100, 1) if total else 0
    return {"latest_date": None, "count": int(have), "detail": f"覆盖 {pct}%（{have}/{total} 只）"}


def _status_news(session) -> dict:
    from datetime import timedelta as _timedelta

    from sqlalchemy import func

    from common.market_time import utc_now
    from data_engine.storage.models import NewsArticle
    # ⛔ 这里必须用 utc_now 而不是 datetime.now()：`published_at` 存的是 naive UTC
    # （`news_engine/fetcher.py` 用 utc_now），拿本地 UTC+8 当 cutoff 会系统性
    # 少算 8 小时的量。老的 `_asset_news` 就是这么写错的（B5 已修）。
    week_ago = utc_now() - _timedelta(days=7)
    last_7d = session.query(func.count(NewsArticle.id)).filter(
        NewsArticle.published_at >= week_ago,
    ).scalar() or 0
    total = session.query(func.count(NewsArticle.id)).scalar() or 0
    latest = session.query(func.max(NewsArticle.published_at)).scalar()
    return {"latest_date": latest.date() if latest else None, "count": int(last_7d),
            "detail": f"近 7 天 {last_7d} 条 · 库存 {total} 条"}


def _status_realtime(session) -> dict:
    """实时快照状态。

    ⚠️ **条数必须用子查询在 SQL 里比，不能把 max() 取回 Python 再回填过滤。**
    `snapshot_time` 是 DateTime，SQLite 存的是 `'2026-07-07 05:07:37'`；取回来
    是 `datetime(...)`，再绑回去 SQLAlchemy 会渲染成 `'2026-07-07 05:07:37.000000'`
    —— 带微秒，字符串比不上，**count 恒为 0**。实测：库里明明有 6594 行，
    面板上一直显示 0 只。老的 `api/routes/data_monitor._asset_realtime` 就是这么写的
    （同一个 bug，B5 一并修了）。
    """
    from sqlalchemy import func

    from data_engine.storage.models import RealtimeSnapshot
    latest = session.query(func.max(RealtimeSnapshot.snapshot_time)).scalar()
    count = 0
    if latest:
        newest = session.query(
            func.max(RealtimeSnapshot.snapshot_time),
        ).scalar_subquery()
        count = session.query(func.count(RealtimeSnapshot.id)).filter(
            RealtimeSnapshot.snapshot_time == newest,
        ).scalar() or 0
    return {"latest_date": latest.date() if latest else None, "count": int(count),
            "detail": f"最新快照 {count} 只"}


def _status_signals(session) -> dict:
    from sqlalchemy import func

    from data_engine.storage.models import Signal
    from data_engine.trading_calendar import _to_date
    latest = _to_date(session.query(func.max(Signal.date)).scalar())
    count = 0
    if latest:
        count = session.query(func.count(Signal.id)).filter(Signal.date == latest).scalar() or 0
    return {"latest_date": latest, "count": int(count), "detail": f"最新一日 {count} 条信号"}


def _status_tracking(session) -> dict:
    from sqlalchemy import func

    from data_engine.storage.models import SignalTracking
    total = session.query(func.count(SignalTracking.id)).scalar() or 0
    latest = session.query(func.max(SignalTracking.updated_at)).scalar()
    return {"latest_date": latest.date() if latest else None, "count": int(total),
            "detail": f"{total} 条追踪记录"}


def _status_decision_outcome(session) -> dict:
    from sqlalchemy import func

    from data_engine.storage.models import DecisionLog
    total = session.query(func.count(DecisionLog.id)).scalar() or 0
    done = session.query(func.count(DecisionLog.id)).filter(
        DecisionLog.outcome_status == "completed",
    ).scalar() or 0
    latest = session.query(func.max(DecisionLog.created_at)).scalar()
    return {"latest_date": latest.date() if latest else None, "count": int(done),
            "detail": f"已评出 {done}/{total} 条建议"}


def _probe_limit_up(session, start: date, end: date) -> dict[date, int]:
    from sqlalchemy import func

    from data_engine.storage.models import LimitUpPool
    from data_engine.trading_calendar import _to_date
    rows = (session.query(LimitUpPool.trade_date, func.count(LimitUpPool.id))
            .filter(LimitUpPool.trade_date >= start, LimitUpPool.trade_date <= end)
            .group_by(LimitUpPool.trade_date).all())
    out: dict[date, int] = {}
    for raw_day, count in rows:
        d = _to_date(raw_day)
        if d:
            out[d] = int(count)
    return out


# ====================================================================
# 注册表本体
# ====================================================================

_ASSETS: tuple[DataAsset, ...] = (
    # ---- 日历（优先级最高：缺口判定的前提）----
    DataAsset(
        key=f"calendar.{A_SHARE}", label="A股交易日历", group="calendar", market=A_SHARE,
        cadence="daily_after_close", runner=_run_calendar(A_SHARE),
        status_probe=_status_calendar(A_SHARE),
        hint="上证指数自证的交易日历，判缺口的前提。A股从存量日线自举，零网络请求",
    ),
    DataAsset(
        key=f"calendar.{HK_STOCK}", label="港股交易日历", group="calendar", market=HK_STOCK,
        cadence="daily_after_close", runner=_run_calendar(HK_STOCK),
        status_probe=_status_calendar(HK_STOCK),
        hint="恒生指数自证的交易日历",
    ),
    DataAsset(
        key=f"calendar.{US_STOCK}", label="美股交易日历", group="calendar", market=US_STOCK,
        cadence="daily_after_close", runner=_run_calendar(US_STOCK),
        status_probe=_status_calendar(US_STOCK),
        hint="标普500自证的交易日历",
    ),

    # ---- 行情 ----
    DataAsset(
        key="daily.a_share", label="A股日线", group="quote", market=A_SHARE,
        cadence="daily_after_close", runner=_run_daily_a_share,
        depends_on=(f"calendar.{A_SHARE}",),
        supports_gap_fill=True, gap_probe=_probe_daily(A_SHARE),
        enabled_env="DAILY_AUTO_UPDATE_ENABLED",
        hint="东财批量+慢路径，已最新的自动跳过",
    ),
    DataAsset(
        key="daily.hk_stock", label="港股日线", group="quote", market=HK_STOCK,
        cadence="daily_after_close", runner=_run_daily_overseas(HK_STOCK),
        depends_on=(f"calendar.{HK_STOCK}",),
        supports_gap_fill=True, gap_probe=_probe_daily(HK_STOCK),
        enabled_env="OVERSEAS_AUTO_UPDATE_ENABLED",
        hint="yfinance 批量，16:30 独立 cron（港股 16:00 才收盘）",
    ),
    DataAsset(
        key="daily.us_stock", label="美股日线", group="quote", market=US_STOCK,
        cadence="daily_after_close", runner=_run_daily_overseas(US_STOCK),
        depends_on=(f"calendar.{US_STOCK}",),
        supports_gap_fill=True, gap_probe=_probe_daily(US_STOCK),
        enabled_env="OVERSEAS_AUTO_UPDATE_ENABLED",
        hint="yfinance 批量，与港股共抢一把 Yahoo 锁",
    ),
    DataAsset(
        key="kline.crypto", label="加密日线", group="quote", market=CRYPTO,
        cadence="interval", runner=_run_crypto_kline,
        supports_gap_fill=True, gap_probe=_probe_daily(CRYPTO),
        enabled_env="CRYPTO_SCHEDULER_ENABLED",
        hint="币安 7×24，每 30 分钟一轮；当日未收盘那根刻意丢弃",
    ),
    DataAsset(
        key="realtime.a_share", label="A股实时快照", group="quote", market=A_SHARE,
        cadence="on_demand", runner=_dict_runner("realtime.a_share", A_SHARE, _run_realtime),
        in_update_all=False, status_probe=_status_realtime,
        hint="盘中快照，非积累型数据，不进一键全量",
    ),

    # ---- 基本面 ----
    DataAsset(
        key="financial.a_share", label="A股财报", group="fundamental", market=A_SHARE,
        cadence="daily_after_close", runner=_run_financial,
        status_probe=_status_financial,
        hint="增量模式跳过 150 天内已有报告期的股票",
    ),
    DataAsset(
        key="valuation.a_share", label="A股估值", group="fundamental", market=A_SHARE,
        cadence="daily_after_close",
        runner=_dict_runner("valuation.a_share", A_SHARE, _run_valuation,
                            field_map={"saved": "records", "total": "updated"}),
        depends_on=("daily.a_share",), status_probe=_status_valuation,
        hint="⚠️ 只能从当天续，历史快照不可回填，所以不参与缺口补齐",
    ),
    DataAsset(
        key="industry.a_share", label="A股行业", group="fundamental", market=A_SHARE,
        cadence="on_demand",
        runner=_dict_runner("industry.a_share", A_SHARE, _run_industry,
                            field_map={"updated": "updated", "fetched": "records"}),
        in_update_all=False, status_probe=_status_industry,
        hint="行业映射基本不变，按需跑；resume 模式只补缺失的",
    ),

    # ---- 情绪 ----
    DataAsset(
        key="news.all", label="新闻", group="sentiment", market=None,
        cadence="interval",
        runner=_dict_runner("news.all", None, _run_news),
        status_probe=_status_news,
        enabled_env="NEWS_AUTO_FETCH_ENABLED",
        hint="每 15 分钟抓取 + 情绪分析",
    ),

    # ---- 衍生（依赖行情）----
    DataAsset(
        key="limit_up.a_share", label="涨停池", group="derived", market=A_SHARE,
        cadence="daily_after_close",
        runner=_dict_runner("limit_up.a_share", A_SHARE, _run_limit_up),
        depends_on=("daily.a_share",),
        supports_gap_fill=True, gap_probe=_probe_limit_up, gap_mode="presence",
        hint="涨停/炸板池抓取 + 候选打分",
    ),
    DataAsset(
        key="signals", label="信号回补", group="derived", market=None,
        cadence="daily_after_close", runner=_run_signals,
        depends_on=("daily.a_share",),
        # ⛔ **刻意不接通用缺口扫描**。`SignalGenerator.backfill_signals_stream`
        # 自带 `detect_signal_gaps`（「有行情但无信号的交易日」），比通用的
        # 覆盖数比对精确得多，而且 lookback 已按下游标杆落后天数自适应。
        # 再套一层通用扫描只会两套判据打架、重复触发。
        status_probe=_status_signals,
        enabled_env="DAILY_AUTO_UPDATE_CHAIN_SIGNALS",
        hint="检测有行情无信号的交易日并回补；lookback 按落后天数自适应（自带缺口检测）",
    ),
    DataAsset(
        key="tracking", label="信号追踪", group="derived", market=None,
        cadence="daily_after_close",
        runner=_dict_runner("tracking", None, _run_tracking,
                            field_map={"updated": "updated", "created": "records"}),
        depends_on=("signals",), status_probe=_status_tracking,
        enabled_env="DAILY_AUTO_UPDATE_CHAIN_TRACKING",
        hint="更新未完成信号的追踪状态",
    ),
    DataAsset(
        key="decision_outcome", label="决策后验", group="derived", market=None,
        cadence="daily_after_close",
        runner=_dict_runner("decision_outcome", None, _run_decision_outcome),
        depends_on=("daily.a_share", "daily.hk_stock", "daily.us_stock", "kline.crypto"),
        status_probe=_status_decision_outcome,
        enabled_env="DAILY_AUTO_UPDATE_CHAIN_DECISION_OUTCOME",
        hint="回填 AI 建议的后验结果（「上周说买茅台，对了吗」）",
    ),
)

_BY_KEY: dict[str, DataAsset] = {a.key: a for a in _ASSETS}


# ====================================================================
# 查询接口
# ====================================================================


def all_assets() -> tuple[DataAsset, ...]:
    return _ASSETS


def get_asset(key: str) -> DataAsset:
    if key not in _BY_KEY:
        raise KeyError(f"未知数据资产: {key}（已登记 {sorted(_BY_KEY)}）")
    return _BY_KEY[key]


def has_asset(key: str) -> bool:
    return key in _BY_KEY


def assets_for(*, cadence: Cadence | None = None, market: str | None = None,
               group: Group | None = None, in_update_all: bool | None = None,
               gap_fillable: bool | None = None,
               only_enabled: bool = True) -> list[DataAsset]:
    """按条件筛资产。默认只返回 env 门控开着的。"""
    out = []
    for a in _ASSETS:
        if cadence and a.cadence != cadence:
            continue
        if market and a.market != market:
            continue
        if group and a.group != group:
            continue
        if in_update_all is not None and a.in_update_all != in_update_all:
            continue
        if gap_fillable is not None and a.supports_gap_fill != gap_fillable:
            continue
        if only_enabled and not a.enabled():
            continue
        out.append(a)
    return out


def topo_sort(keys: list[str]) -> list[str]:
    """按 `depends_on` 拓扑排序。**依赖不在 keys 里就当它已经满足**（跳过，不报错）——
    「只更新美股日线」时不该被 `calendar.us_stock` 之外的东西牵连。

    Raises:
        ValueError: 存在循环依赖（配置错误，必须当场炸而不是静默乱序）。
    """
    wanted = [k for k in keys if k in _BY_KEY]
    pending = set(wanted)
    out: list[str] = []
    while pending:
        ready = [k for k in wanted
                 if k in pending
                 and not (set(_BY_KEY[k].depends_on) & pending)]
        if not ready:
            raise ValueError(f"数据资产存在循环依赖: {sorted(pending)}")
        # 保持调用方给的相对顺序，只把被依赖的提前
        for k in ready:
            out.append(k)
            pending.discard(k)
    return out


def update_all_keys() -> list[str]:
    """「一键更新全部」要跑的资产，已拓扑排序。"""
    return topo_sort([a.key for a in assets_for(in_update_all=True)])


def describe() -> list[dict]:
    """注册表快照 —— 前端资产矩阵的数据源。"""
    return [
        {
            "key": a.key,
            "label": a.label,
            "group": a.group,
            "market": a.market,
            "cadence": a.cadence,
            "depends_on": list(a.depends_on),
            "in_update_all": a.in_update_all,
            "supports_gap_fill": a.supports_gap_fill,
            "enabled": a.enabled(),
            "hint": a.hint,
        }
        for a in _ASSETS
    ]


def _self_check() -> None:
    """启动自检：依赖是否都登记了、key 是否重复、拓扑是否成环。

    在模块导入时跑 —— 注册表配错是**开发期错误**，就该在启动时当场炸，
    而不是等到某次定时任务凌晨三点悄悄跑歪。
    """
    if len(_BY_KEY) != len(_ASSETS):
        raise ValueError("数据资产 key 有重复")
    for a in _ASSETS:
        for dep in a.depends_on:
            if dep not in _BY_KEY:
                raise ValueError(f"资产 {a.key} 依赖了未登记的 {dep}")
        if a.supports_gap_fill and a.gap_probe is None:
            raise ValueError(f"资产 {a.key} 声明支持补洞但没有 gap_probe")
    topo_sort([a.key for a in _ASSETS])


_self_check()
