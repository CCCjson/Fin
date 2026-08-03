"""股票策略的 tick 调度（S5）。

## ⭐ 与 crypto 调度器最大的不同：**闭市不跑**

crypto 是 7×24，所以 `crypto_strategy/scheduler.py` 用固定间隔常转。
股票不是：A 股一天 4 小时、美股还差 12 个时区。闭市时段跑 tick 有三个害处：

1. 拿到的是**上一个交易日的收盘数据**，DSL 条件可能因此命中 → 排出一批
   开盘就作废的单；
2. 白白打分析卡那条链路（每次都要查库 + 算指标）；
3. 让「last_run_at」这类节奏判定失真。

判据走 `common/market_session`（S6 建的真源），⛔ 别在这里手写交易时段 ——
那需要一份时区表 + 节假日规则，算错了还看不出来。

## 🔒 一天一次的日切

T+1 的解冻（`settle_t1`）**一天只能调一次**。⛔ 别塞进 tick ——
那等于取消了 T+1，纸面成绩会重新变得比实盘好看。
本调度器按「市场当地日」记账，跨日时调一次。
"""
from typing import Any

from loguru import logger

from common.market_session import is_open
from common.market_time import market_today

# 股票分析卡不便宜（查库 + 算指标 + 情绪），比 crypto 的 tick 慢得多。
# ⚠️ 每条策略自己的 `interval_minutes` 决定它到不到期，这里只是高频巡检。
_SWEEP_SECONDS = 60


class StockStrategyScheduler:
    """把 `run_tick` 定时跑起来。

    ⚠️ 只服务股票市场（a_share / us_stock）。crypto 有它自己的调度器 ——
    两者合并是 S5 §3c 记着的欠债之一。
    """

    def __init__(self):
        self._scheduler: Any = None
        self._last_settled: dict[str, Any] = {}   # market → 上次日切的当地日

    # ── 生命周期 ──

    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def start(self, force: bool = False) -> dict:
        from apscheduler.triggers.interval import IntervalTrigger

        if self.is_running() and not force:
            return {"started": False, "reason": "已在运行"}
        sch = self._get_scheduler()
        if not sch.running:
            sch.add_job(self.sweep, IntervalTrigger(seconds=_SWEEP_SECONDS),
                        id="stock_strategy_sweep", max_instances=1,
                        replace_existing=True, coalesce=True)
            sch.start()
        return {"started": True, "sweep_seconds": _SWEEP_SECONDS}

    def stop(self) -> dict:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        return {"stopped": True}

    def _get_scheduler(self):
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self._scheduler = AsyncIOScheduler(timezone="UTC")
        return self._scheduler

    # ── 一轮巡检 ──

    def sweep(self) -> dict:
        """遍历启用中的股票策略，到期且开市的跑一轮。

        ⚠️ 单条策略出错**不中断整轮** —— 否则一条坏策略会让别的策略整天不交易。
        """
        out: list[dict] = []
        for row in self._enabled_stock_strategies():
            try:
                out.append(self.tick_one(row))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[stock-sched] {row.strategy_id} 本轮异常: {e}")
                out.append({"strategy_id": row.strategy_id, "skipped": f"异常：{e}"})
        return {"checked": len(out), "results": out}

    def tick_one(self, row) -> dict:
        """单条策略：判开市 → 判到期 → 日切 → 跑一轮。"""
        from crypto_strategy.service import spec_from_row

        spec = spec_from_row(row)
        market = spec.market

        # ⭐ 闭市不跑：拿到的是上一个交易日的收盘数据，命中了也只会排出
        #    开盘就作废的单。判据走 S6 的真源。
        if not is_open(market):
            return {"strategy_id": row.strategy_id, "skipped": f"{market} 未开市"}

        if not self._is_due(row, spec):
            return {"strategy_id": row.strategy_id, "skipped": "未到 tick 间隔"}

        adapter = self._adapter_for(spec)
        self._settle_if_new_day(market, adapter)

        from strategy_runtime.executor import run_tick
        from trading_engine.risk.adapter import get_total_capital

        res = run_tick(spec, adapter, capital=get_total_capital(),
                       strategy_id=row.strategy_id, mode=row.mode or "paper")
        self._touch(row.strategy_id)
        return {"strategy_id": row.strategy_id, **res}

    # ── 细节 ──

    @staticmethod
    def _enabled_stock_strategies() -> list:
        """启用中的**股票**策略。

        ⚠️ 必须按 `market` 过滤：crypto 策略有它自己的引擎，
        两边都跑一遍的话同一条策略会被下两次单。
        """
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategy

        session = get_session()
        try:
            rows = session.query(CryptoStrategy).filter(
                CryptoStrategy.enabled == 1,
                CryptoStrategy.market.in_(("a_share", "us_stock"))).all()
            session.expunge_all()
            return rows
        finally:
            session.close()

    @staticmethod
    def _is_due(row, spec) -> bool:
        from datetime import timedelta

        from common.market_time import utc_now
        last = getattr(row, "last_run_at", None)
        if last is None:
            return True
        return utc_now() - last >= timedelta(minutes=max(1, spec.interval_minutes))

    @staticmethod
    def _touch(strategy_id: str) -> None:
        from common.market_time import utc_now
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategy

        session = get_session()
        try:
            row = session.query(CryptoStrategy).filter(
                CryptoStrategy.strategy_id == strategy_id).first()
            if row is not None:
                row.last_run_at = utc_now()
                session.commit()
        except Exception as e:  # noqa: BLE001 — 记时间失败不该掀翻已经下出去的单
            logger.warning(f"[stock-sched] {strategy_id} last_run_at 更新失败: {e}")
        finally:
            session.close()

    @staticmethod
    def _adapter_for(spec):
        from strategy_runtime.stock_adapter import StockAdapter
        from trading_engine.brokers.paper_broker import get_paper_broker

        # ⚠️ 目前只有 PaperBroker。真券商是可插拔后端（Jason 2026-08-02：
        #    先建地基、券商后接）——到时只改这一处，执行器一行不用动。
        return StockAdapter(get_paper_broker()).for_spec(spec)

    def _settle_if_new_day(self, market: str, adapter) -> None:
        """跨到新的交易日就解冻一次 T+1 持仓。

        🔒 **一天只能调一次**。⛔ 别塞进每次 tick —— 那等于取消 T+1。
        ⚠️ 按**市场当地日**判（美股的 8/2 不是北京时间的 8/2）。
        """
        today = market_today(market)
        if self._last_settled.get(market) == today:
            return
        adapter.settle_new_day()
        self._last_settled[market] = today
        logger.info(f"[stock-sched] {market} 日切：T+1 持仓已解冻（{today}）")


stock_strategy_scheduler = StockStrategyScheduler()
