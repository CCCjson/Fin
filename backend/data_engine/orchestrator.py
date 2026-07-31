"""编排器 —— 所有数据更新的**唯一执行入口**。

## 为什么要收成一个入口

此前同一件事有三条路，各自实现一遍：

    定时       daily_pipeline_scheduler._run_pipeline_sync()  硬编码 6 步
    手动       POST /data/update-daily/stream                 只管 A 股日线
    统一刷新   market_refresh.refresh_stream()                只管股票三市场日线

三条路的顺序、失败处理、日志落库各写各的，港美股/crypto/新闻压根不在任何一条里。
现在它们都调 `run_assets()`，差别只剩「跑哪些资产」这一个参数。

## 三条不变式

1. **按 `depends_on` 拓扑排序** —— 日线必须在信号之前，信号必须在追踪之前，
   决策后验必须在所有行情之后（它读 DailyQuote 判建议日之后的走势，放前面会
   永远少最新一根 bar，实测过：库里停在 07-09 时 07-10 发的建议全判 no_quotes）。
2. **单个资产失败只记 warning，不中断后续** —— 沿用主链既有语义。一个数据源挂了
   不该让整晚的更新全泡汤。
3. **每个资产跑完写一行 `DataUpdateLog`** —— 此前只有部分 updater 写，所以
   「最近更新日志」一直是残缺的，看不出港美股/crypto/新闻跑没跑。
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date

from loguru import logger

from common.market_time import utc_now
from data_engine.events import summarize
from data_engine.registry import RunContext, get_asset, topo_sort
from data_engine.storage.database import get_session
from data_engine.storage.models import DataUpdateLog


def run_asset(key: str, ctx: RunContext | None = None,
              on_event: Callable[[dict], None] | None = None) -> dict:
    """跑单个资产，返回终态摘要。事件通过 `on_event` 回调实时吐出。

    **不抛异常** —— runner 内部炸了也翻译成 `{"status": "failed"}`。调用方是定时
    任务和后台 job，它们绝不能因为一个资产挂掉而整体退出。
    """
    asset = get_asset(key)
    ctx = ctx or RunContext()
    started = utc_now()
    events: list[dict] = []

    try:
        for evt in asset.runner(ctx):
            events.append(evt)
            if on_event:
                on_event(evt)
    except Exception as e:  # noqa: BLE001 — 见 docstring：绝不向上抛
        logger.exception(f"[编排] {key} 执行异常: {e}")
        evt = {"event": "error", "asset": key, "market": asset.market, "message": str(e)}
        events.append(evt)
        if on_event:
            on_event(evt)

    summary = summarize(events)
    summary["asset"] = key
    summary["market"] = asset.market
    summary["label"] = asset.label
    completed = utc_now()
    summary["duration_seconds"] = round((completed - started).total_seconds(), 1)

    _log_run(asset, summary, started, completed)
    return summary


def _log_run(asset, summary: dict, started, completed) -> None:
    """写一行 `DataUpdateLog`。

    ⛔ **写日志失败绝不能影响更新本身** —— 数据已经落库了，日志只是留痕。
    `update_type` 用 `DataAsset.key` 而不是老的 `daily`/`financial` 枚举：
    老枚举根本表达不了「哪个市场的哪种资产」，港美股和 A 股日线都写 `daily`，
    在日志表里分不开。老值仍然存在于历史行里，前端两种都要能显示。
    """
    session = get_session()
    try:
        session.add(DataUpdateLog(
            market=asset.market or "all",
            update_type=asset.key,
            symbols_count=summary.get("updated"),
            records_count=summary.get("records"),
            status=summary.get("status") or "unknown",
            error_message=(summary.get("note") or None)
            if summary.get("status") in ("failed", "partial") else None,
            started_at=started,
            completed_at=completed,
            duration_seconds=summary.get("duration_seconds"),
        ))
        session.commit()
    except Exception as e:  # noqa: BLE001
        session.rollback()
        logger.warning(f"[编排] 写 DataUpdateLog 失败（不影响更新）: {e}")
    finally:
        session.close()


def run_assets(keys: list[str], *, mode: str = "incremental",
               today: date | None = None,
               should_stop: Callable[[], bool] = lambda: False,
               on_event: Callable[[dict], None] | None = None,
               on_asset_done: Callable[[dict], None] | None = None) -> dict:
    """按拓扑顺序依次跑一组资产。

    Args:
        keys: 资产 key 列表（会被拓扑排序，未登记的自动忽略）。
        mode: `incremental` 或 `gap_fill`。
        should_stop: 协作式停止 —— **在资产之间检查**，不打断正在跑的那个
            （写库写到一半被打断会留半截事务）。
        on_event: 每个契约事件回调（前端进度）。
        on_asset_done: 每个资产跑完回调（前端把那一行标成完成）。

    Returns:
        `{"ok", "started_at", "completed_at", "assets": {key: summary}, "order": [...]}`
    """
    order = topo_sort(keys)
    started = utc_now()
    out: dict = {"ok": True, "order": order, "assets": {},
                 "started_at": started.isoformat()}

    logger.info(f"[编排] 开跑 {len(order)} 个资产（mode={mode}）: {order}")
    for key in order:
        if should_stop():
            logger.info(f"[编排] 收到停止信号，剩余 {len(order) - len(out['assets'])} 个资产不再处理")
            out["stopped"] = True
            break
        ctx = RunContext(mode=mode, today=today, should_stop=should_stop)
        summary = run_asset(key, ctx, on_event=on_event)
        out["assets"][key] = summary
        if summary.get("status") in ("failed", "unknown"):
            out["ok"] = False
        if on_asset_done:
            on_asset_done(summary)

    out["completed_at"] = utc_now().isoformat()
    ok_n = sum(1 for s in out["assets"].values() if s.get("status") == "success")
    logger.info(f"[编排] 完成：{ok_n}/{len(out['assets'])} 个资产成功")
    _publish_done(out, mode)
    return out


def _publish_done(result: dict, mode: str) -> None:
    """发业务事件到总线（供 MoneyBill 读 / 前端活动流展示）。沿用主链既有行为。"""
    try:
        from business_events import PIPELINE_DONE, publish_event
        publish_event(
            PIPELINE_DONE, source="orchestrator",
            severity=("info" if result.get("ok") else "warn"),
            title=("数据更新完成" if result.get("ok") else "数据更新完成（有资产失败）"),
            ok=result.get("ok"), mode=mode, assets=list(result.get("assets", {})),
        )
    except Exception:  # noqa: BLE001 — 发事件失败不该影响任何东西
        pass


def stream_assets(keys: list[str], *, mode: str = "incremental",
                  today: date | None = None) -> Iterator[dict]:
    """流式版本 —— 给还在用 NDJSON 流的调用方（老端点兼容）。

    生成器天然是「拉」模型，`run_assets` 是「推」模型（回调），两者不能互相包装
    而不引入线程。这里直接展开循环，逻辑与 `run_assets` 保持一致。
    """
    order = topo_sort(keys)
    yield {"event": "plan", "assets": order, "mode": mode}
    for key in order:
        asset = get_asset(key)
        ctx = RunContext(mode=mode, today=today)
        started = utc_now()
        events: list[dict] = []
        try:
            for evt in asset.runner(ctx):
                events.append(evt)
                yield evt
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[编排] {key} 执行异常: {e}")
            evt = {"event": "error", "asset": key, "market": asset.market,
                   "message": str(e)}
            events.append(evt)
            yield evt
        summary = summarize(events)
        summary["asset"] = key
        completed = utc_now()
        summary["duration_seconds"] = round((completed - started).total_seconds(), 1)
        _log_run(asset, summary, started, completed)
    yield {"event": "all_complete", "assets": order}
