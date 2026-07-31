"""数据更新的**统一事件契约** —— 所有 runner 说同一种话。

## 为什么要有它

三个 updater 各说各话，同一个语义三个名字：

    DailyUpdater          → skipped_fresh / success / new_records
    OverseasDailyUpdater  → skipped_fresh / updated  / rows
    financial_updater     → skipped       / success  / new_records

于是前端 `ProgressCard` 只能写成 `progress.skipped ?? progress.skipped_fresh`
这种猜谜代码，而且每接一个新资产就要再猜一次。注册表要把 N 个资产统一编排、
统一渲染，就必须先让它们的输出长一个样。

## 契约

    {
      "event":        start | progress | complete | error | skipped,
      "asset":        "daily.a_share",      # DataAsset.key
      "market":       "a_share" | None,
      "total":        本次**待更新**数量（⛔ 不含 fresh_skipped）,
      "current":      已处理数量,
      "fresh_skipped":已最新、主动跳过的数量,
      "updated":      成功更新的标的数,
      "failed":       失败的标的数,
      "records":      写入的记录行数,
      "note":         人读的当前状态,
      ...             # 资产独有字段原样透传
    }

**`total` 的口径是「待更新」不是「全市场」** —— 5201 只里 5100 只已最新时，
进度条应该是 `101/101` 走满，而不是 `101/5201` 卡在 2%。这也是现有 A 股
updater 的口径（`total_need_update`），统一沿用。

## 为什么用适配而不是改 updater

`normalize()` 是个纯函数，把老 updater 的事件翻译成新契约。**刻意不去改三个
updater 内部**：它们各自带着几十条踩坑注释和实测出来的边界条件（盘中脏数据判定、
frontier skip、代理熔断……），为了改个字段名去动它们，回归风险远大于收益。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

# 契约版本。**改字段语义 = 必须 bump**（同 `market_freshness.FRESHNESS_VERSION`
# 的道理）：不 bump 就是让新旧口径的进度数字混在一起，而且你永远不知道。
EVENT_VERSION = "data-event-v1"

EVENT_KINDS = ("start", "progress", "complete", "error", "skipped")

# 老字段名 → 契约字段名。同义词按**优先级从高到低**排列，取第一个出现的。
_ALIASES: dict[str, tuple[str, ...]] = {
    "fresh_skipped": ("fresh_skipped", "skipped_fresh", "skipped"),
    "updated": ("updated", "success"),
    "records": ("records", "rows", "new_records"),
    "failed": ("failed",),
    "total": ("total",),
    "current": ("current",),
}

# 归一后不再单独保留的老键（避免同一个数字在事件里出现两遍，前端又开始猜）
_CONSUMED = {"skipped_fresh", "skipped", "success", "rows", "new_records"}


def make_event(kind: str, asset: str, market: str | None = None, **fields: Any) -> dict:
    """构造一个符合契约的事件。新写的 runner 直接用这个，不要手搓 dict。"""
    if kind not in EVENT_KINDS:
        raise ValueError(f"未知事件类型: {kind}（只接受 {EVENT_KINDS}）")
    evt: dict[str, Any] = {"event": kind, "asset": asset}
    if market:
        evt["market"] = market
    evt.update(fields)
    return evt


def normalize(raw: dict, asset: str, market: str | None = None) -> dict:
    """把老 updater 的事件翻译成统一契约。

    Args:
        raw: 老 updater yield 出来的 dict（已 json.loads）。
        asset: 归属的 `DataAsset.key`。
        market: 归属市场；`raw` 里已有 `market` 时以 raw 为准（港美股的事件自带）。

    Returns:
        契约形状的新 dict。**未被 `_ALIASES` 覆盖的键原样透传** ——
        `backfilled_stocks`、`proxy_state`、`abort_reason` 这些资产独有的字段
        前端还在用，不能丢。
    """
    out: dict[str, Any] = {
        "event": raw.get("event", "progress"),
        "asset": asset,
    }
    resolved_market = raw.get("market") or market
    if resolved_market:
        out["market"] = resolved_market

    for canonical, candidates in _ALIASES.items():
        for name in candidates:
            if raw.get(name) is not None:
                out[canonical] = raw[name]
                break

    for key, value in raw.items():
        if key in ("event", "asset", "market") or key in _CONSUMED:
            continue
        if key in _ALIASES:
            continue
        out.setdefault(key, value)
    return out


def normalize_stream(lines: Iterator[str], asset: str,
                     market: str | None = None) -> Iterator[dict]:
    """把 NDJSON 行流（老 updater 的 `*_stream()` 输出）翻译成契约事件流。

    解析不了的行**跳过而不是抛** —— 一行脏输出不该掀翻整个资产的更新
    （沿用 `market_refresh._inject_market` 的既有语义）。
    """
    for line in lines:
        if not line or not line.strip():
            continue
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        yield normalize(raw, asset, market)


def summarize(events: list[dict]) -> dict:
    """从一个资产的事件流里提炼终态摘要（给 DataUpdateLog / 前端历史用）。

    取**最后一个 complete/error 事件**为准；一个都没有则认定为异常终止
    （⚠️ 不是「成功但没数据」—— 那两种情况必须能区分，否则静默失败会被
    当成正常完成，正是「连丢 6 个交易日没人发现」那一类病）。
    """
    terminal = None
    for evt in events:
        if evt.get("event") in ("complete", "error", "skipped"):
            terminal = evt
    if terminal is None:
        return {"status": "unknown", "note": "未收到终态事件（疑似异常中断）"}
    kind = terminal.get("event")
    status = {"complete": "success", "error": "failed", "skipped": "skipped"}[kind]
    if kind == "complete" and terminal.get("failed"):
        status = "partial"
    return {
        "status": status,
        "updated": terminal.get("updated") or 0,
        "fresh_skipped": terminal.get("fresh_skipped") or 0,
        "failed": terminal.get("failed") or 0,
        "records": terminal.get("records") or 0,
        "note": terminal.get("message") or terminal.get("note") or "",
    }
