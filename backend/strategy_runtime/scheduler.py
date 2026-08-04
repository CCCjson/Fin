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

        # 🔒 **没配本金就不跑**（Jason 2026-08-04：未配置的市场 fail-closed）。
        #    ⛔ 别回落到 `get_total_capital()` —— 那是**A 股口径的人民币**，
        #    拿它给美股算仓位是错的口径，而且不会有任何报错。
        #    ⚠️ 这条要在**日切之前**判：日切会解冻 T+1 持仓，是有副作用的动作，
        #    不该为一个根本不会跑的 tick 做。
        from common.market import label_of
        from strategy_runtime.stock_adapter import live_broker_for
        from trading_engine.risk.adapter import get_market_capital

        # 🔒 **没接真券商的股票市场不跑 live**（与 `service.arm` 同一判据）。
        #    arm 那道闸只管**新的**上线动作，管不到已经是 `mode="live"` 的存量行
        #    （本轮之前 arm 的、或直接改库改出来的）—— 它们每 tick 都会往
        #    `strategy_trades` 写 `mode="live"` 的成交，而成交其实是纸面的，
        #    S4 的提案打分会拿它当**真钱证据**。⛔ 假真钱战绩比没有战绩糟得多。
        if (row.mode or "paper") == "live" and live_broker_for(market) is None:
            logger.warning(f"[stock-sched] {row.strategy_id} 跳过："
                           f"{label_of(market)}还没接真券商，不跑 live")
            return {"strategy_id": row.strategy_id,
                    "skipped": (f"{label_of(market)}还没接真券商，这条 live 策略不跑 —— "
                                f"跑了也只是纸面成交，却会记成真钱战绩。"
                                f"要观察请改成纸面模式。")}

        capital = get_market_capital(market)
        if capital is None:
            # ⚠️ 必须 log：`tick_one` 的返回值只流到 `sweep()`，而 `sweep()` 是
            #    APScheduler 的 job，**返回值被丢弃** —— 不 log 的话股票这边
            #    就是彻底安静地不交易（crypto 那边至少有 warning + run 行）。
            #    频率不高：这道闸在 `_is_due` 之后，最多每 interval_minutes 一条。
            logger.warning(f"[stock-sched] {row.strategy_id} 跳过："
                           f"{label_of(market)}还没配置本金")
            return {"strategy_id": row.strategy_id,
                    "skipped": (f"{label_of(market)}还没配置本金 —— 去设置页填"
                                f"「{label_of(market)}本金」。在那之前这个市场"
                                f"一单都不会下。")}

        adapter = self._adapter_for(spec)
        self._settle_if_new_day(market, adapter)

        from strategy_runtime.executor import run_tick

        res = run_tick(spec, adapter, capital=capital,
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
        from strategy_runtime.stock_adapter import StockAdapter, live_broker_for
        from trading_engine.brokers.paper_broker import get_paper_broker

        # ⚠️ 真券商是可插拔后端（Jason 2026-08-02：先建地基、券商后接）。
        #    开关点在 `live_broker_for`（**唯一判据**，`service.arm` 那道闸读的是
        #    同一个函数）——它一旦返回实例，这里自动改用它，执行器一行不用动。
        #
        # 🔴 **`mode` 这一条不能少**：`paper` 和 `live` 的区别**全在 broker**
        #    （`executor.run_tick` 的原话），这一层不判的话，真券商上线那天
        #    所有纸面策略会立刻拿真钱下单 —— 纸面策略正是那些**还没验证过**的。
        #    ⛔ 别为了「只改一个函数」的干净把这个判据省掉。
        if spec.mode == "live":
            live = live_broker_for(spec.market)
            if live is not None:
                return StockAdapter(live).for_spec(spec)
        # 🔒 **按市场取**：一个市场一个现金池。发同一个全局实例的话，A 股和美股
        #    共用一笔钱 —— 先跑的那个市场把现金买光，另一个市场就静默下不出单。
        #    ⚠️ 本金未配置时 `get_paper_broker` 会抛，但 `tick_one` 上面那道闸
        #    已经先拦下了；真抛到这儿说明有人把闸挪走了，让它响比静默好。
        return StockAdapter(get_paper_broker(spec.market)).for_spec(spec)

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
