"""统一行情刷新 —— 三个市场一个入口。

## 为什么有这层（2026-07-20）

三个市场的日线更新此前形态不一：A 股是 `DailyUpdater.update_stream()`（NDJSON 流，
有 HTTP 端点 + 前端进度条），港美股只有 `OverseasDailyUpdater`（调度器内部同步跑，
**前端够不着**）。这层把它们收成一个流式入口 `refresh_stream(scope)`，前端一套
消费逻辑、一个按钮管三个市场。

## 「自动检查 / 自动补齐 / 差一天直接更新」怎么落的

这三条**不在这层实现，在各 updater 内部**，这层只做编排：

- **自动检查更新到哪天**：每个 updater 内部查库里 `latest` vs 市场真实最新交易日
  （A 股探东财交易日、港美股探锚点票 frontier）。
- **只补落后的、已最新不白跑**：updater 内部 `todo`/`total_need_update` 为空 → 立即
  complete 秒回。**不需要这层预筛** —— 预筛要么用 `is_stale`（2 天容忍，会漏掉「差
  一天」）、要么自己再探一遍 frontier（重复请求）。交给 updater 内部判最精确。
- **差一天直接更新**：updater 用 frontier/交易日精确到天，差一天就进 `todo`，
  起点 = `latest+1`（差一天就拉一天，不全量）。

所以这层是薄的：查 freshness 给前端看现状 → 依次把各市场的 stream 转发出去。

`scope`：市场名列表（`["a_share"]` 只刷 A 股），或 `None` = 三个都刷。
"""
import json
from collections.abc import Generator, Sequence

from loguru import logger

# 本端点只管**股票三市场**的日线增量刷新。加密货币不在此列 —— 它是 7×24 独立链
# （`data_engine/crypto_scheduler.py` 常转、`crypto_updater.py` 落库），不共享股票的
# 「交易日/收盘/Yahoo 锁」假设，也不该被这个股票刷新按钮编排。故用 canonical 的
# `STOCK_MARKETS`（不含 crypto），而非含 crypto 的 `CANONICAL_MARKETS`。
from common.market import STOCK_MARKETS as _STOCK_MARKETS
from data_engine.storage.database import get_session

_OVERSEAS = ("hk_stock", "us_stock")


def _evt(event: str, **fields) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False, default=str) + "\n"


def _inject_market(line: str, market: str) -> str:
    """A 股 `DailyUpdater.update_stream` 的事件没有 market 字段（它只管 A 股），
    统一转发时补上，前端才能按市场分栏渲染。港美股的事件本就带 market。"""
    try:
        evt = json.loads(line)
        evt.setdefault("market", market)
        return json.dumps(evt, ensure_ascii=False, default=str) + "\n"
    except (json.JSONDecodeError, TypeError):
        return line


def resolve_scope(scope: Sequence[str] | None) -> list[str]:
    """规整 scope → 有序市场列表。None/空 = 股票三市场（canonical 顺序）。

    只接受股票三市场；crypto 走独立链，传进来按未知市场拒绝。
    """
    if not scope:
        return list(_STOCK_MARKETS)
    bad = [m for m in scope if m not in _STOCK_MARKETS]
    if bad:
        raise ValueError(f"未知市场: {bad}（本端点只刷股票 {_STOCK_MARKETS}；加密货币走独立链）")
    # 去重但保留 canonical 顺序，避免调用方乱序/重复
    return [m for m in _STOCK_MARKETS if m in set(scope)]


def _freshness_snapshot() -> dict:
    """三市场当前新鲜度（供 plan 事件展示；判定内核在 common/market_freshness）。"""
    session = get_session()
    try:
        from data_engine.health import get_freshness
        return get_freshness(session)
    except Exception as e:  # noqa: BLE001 — 展示用，查不到不该挡住更新
        logger.warning(f"[统一刷新] 查 freshness 失败（不影响更新）: {e}")
        return {}
    finally:
        session.close()


def refresh_stream(scope: Sequence[str] | None = None) -> Generator[str, None, None]:
    """统一行情刷新，流式 yield NDJSON 事件。

    事件序列：
      `plan`（哪些市场要刷 + 当前 freshness）
      → 每个市场依次 `start`/`progress`/`complete`（或 `error`），都带 `market` 字段
      → `all_complete`（各市场汇总）

    港美股共抢一次 Yahoo 互斥锁（深历史回补也打 Yahoo，见 yf_batch.yahoo_job_lock）。
    """
    markets = resolve_scope(scope)
    yield _evt("plan", markets=markets, freshness=_freshness_snapshot())

    summary: dict[str, dict] = {}
    overseas_targets = [m for m in markets if m in _OVERSEAS]

    for market in markets:
        if market == "a_share":
            yield from _refresh_a_share(summary)
        # 港美股在下面统一处理（要共抢一把 Yahoo 锁），这里跳过
    if overseas_targets:
        yield from _refresh_overseas(overseas_targets, summary)

    yield _evt("all_complete", summary=summary)


def _refresh_a_share(summary: dict) -> Generator[str, None, None]:
    """转发 A 股 DailyUpdater.update_stream，注入 market 字段。"""
    from data_engine.daily_updater import DailyUpdater
    last: dict = {}
    for line in DailyUpdater().update_stream():
        out = _inject_market(line, "a_share")
        try:
            evt = json.loads(out)
            if evt.get("event") in ("complete", "error"):
                last = evt
        except json.JSONDecodeError:
            pass
        yield out
    summary["a_share"] = last


def _refresh_overseas(targets: list[str], summary: dict) -> Generator[str, None, None]:
    """港美股：共抢一次 Yahoo 锁，依次 run_stream。抢不到就 yield skip 事件。"""
    from acquisition.markets.yf_batch import yahoo_job_lock
    from data_engine.overseas_daily_updater import OverseasDailyUpdater

    with yahoo_job_lock("统一刷新-港美股") as ok:
        if not ok:
            for m in targets:
                summary[m] = {"skipped": "Yahoo 长任务互斥（深历史回补正在跑）"}
                yield _evt("skipped", market=m,
                           message="深历史回补正在跑，港美股本次跳过")
            return
        updater = OverseasDailyUpdater()
        for market in targets:
            last: dict = {}
            for line in updater.run_stream(market):
                try:
                    evt = json.loads(line)
                    if evt.get("event") in ("complete", "error"):
                        last = evt
                except json.JSONDecodeError:
                    pass
                yield line
            summary[market] = last
