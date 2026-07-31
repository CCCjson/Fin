"""资产矩阵 —— 「每个资产 × 每个市场，各自更新到哪天、健不健康」。

## 为什么是矩阵不是列表

前端此前是一维的 7 张卡（日线/实时/财报/估值/新闻/涨停/知识库），**每张卡都只
统计 A 股**。港美股的健康状态在整个页面上只有一行「新鲜度条带」，crypto 只有
一个胶囊，而它们各自的财报/新闻/衍生数据压根没有位置。

改成「资产（行）× 市场（列）」的二维网格后，"美股日线落后了" 是一眼可见的，
不用逐张卡去读。数据源就是本模块。

## 健康度判定

    ok     参考交易日 = 日历最近的交易日（或落后在容忍内）
    warn   落后 1 个交易日 / 覆盖率偏低 / 有 suspected 缺口
    stale  落后超过阈值 / 一条数据都没有 / 有 certain 缺口

判「新不新鲜」复用 `common/market_freshness`（中位数基线，⛔ 不用 `max(date)`），
判「缺不缺」复用 `data_gaps` 表。两者互补：新鲜度看尾巴，缺口看中间。
"""
from __future__ import annotations

from datetime import date, timedelta

from loguru import logger
from sqlalchemy import func

from common.market import A_SHARE, CRYPTO
from common.market_time import market_today, utc_iso, utc_now
from data_engine.registry import all_assets

# 落后几个交易日算 warn / stale（口径同 market_freshness.STALE_AFTER_WEEKDAYS）
_WARN_BEHIND = 1
_STALE_BEHIND = 2


def _probe_latest(session, asset) -> tuple[date | None, int | None, str]:
    """这个资产最新一天的日期、条数、一句话详情。

    优先用 `gap_probe`（逐日条数，日线类资产最准）；没有的用 `status_probe`
    （财报/估值/新闻/行业这类不按交易日组织的资产）。两个都没有 → 无从判断。
    """
    if asset.gap_probe is not None:
        market = asset.market or CRYPTO
        today = market_today(market)
        observed = asset.gap_probe(session, today - timedelta(days=30), today)
        if observed:
            latest = max(observed)
            return latest, observed[latest], f"最新一日 {observed[latest]} 条"
        return None, None, "近 30 天无数据"
    if asset.status_probe is not None:
        r = asset.status_probe(session) or {}
        return r.get("latest_date"), r.get("count"), r.get("detail") or ""
    return None, None, ""


def _gap_counts(session) -> dict[str, dict[str, int]]:
    """每个资产的未收口缺口数，按 confidence 分。"""
    from data_engine.storage.models import DataGap
    rows = (session.query(DataGap.asset, DataGap.confidence, func.count(DataGap.id))
            .filter(DataGap.status.in_(("open", "filling", "suspected")))
            .group_by(DataGap.asset, DataGap.confidence).all())
    out: dict[str, dict[str, int]] = {}
    for asset, confidence, count in rows:
        out.setdefault(asset, {"certain": 0, "suspected": 0})[confidence] = int(count)
    return out


def _last_log(session) -> dict[str, dict]:
    """每个资产最近一次 `DataUpdateLog`。

    `update_type` 现在存的是 `DataAsset.key`（编排器写的），老行存的是
    `daily`/`financial` 这类枚举 —— 两种都要认，否则重构当天日志会突然空一片。
    """
    from data_engine.storage.models import DataUpdateLog
    rows = (session.query(DataUpdateLog)
            .order_by(DataUpdateLog.started_at.desc())
            .limit(300).all())
    out: dict[str, dict] = {}
    for lg in rows:
        if lg.update_type in out:
            continue
        out[lg.update_type] = {
            "status": lg.status,
            "completed_at": utc_iso(lg.completed_at),
            "records": lg.records_count,
            "duration_seconds": lg.duration_seconds,
        }
    return out


_LEGACY_LOG_ALIASES = {
    "daily.a_share": ("daily", "daily_incremental"),
    "financial.a_share": ("financial",),
}


def _resolve_log(logs: dict[str, dict], key: str) -> dict | None:
    if key in logs:
        return logs[key]
    for legacy in _LEGACY_LOG_ALIASES.get(key, ()):
        if legacy in logs:
            return logs[legacy]
    return None


def _health(asset, latest: date | None, gaps: dict[str, int],
            behind: int | None, count: int | None = None,
            fallback: tuple[str, str] | None = None) -> tuple[str, str]:
    """(健康度, 一句话原因)。

    `fallback` 是「交易日历不可用时的独立判据结论」。`behind` 算不出来时**必须**
    用它，不能直接落到 ok —— 见 `_behind_trading_days` 里那段「陈旧的日历会掩盖
    陈旧的数据」。
    """
    if gaps.get("certain"):
        return "stale", f"有 {gaps['certain']} 天缺口待补"
    if latest is None:
        # 有些资产**天生没有日期概念**（行业映射就是一张 symbol→行业的表，
        # 不按交易日组织）。它们 `latest_date=None` 是正常的，只要有量就算健康 ——
        # 不加这一条，行业覆盖 99.7% 也会被判成 stale。
        if count:
            return "ok", "正常"
        if asset.gap_probe or asset.status_probe:
            return "stale", "一条数据都没有"
        return "unknown", "无状态探测"
    if behind is not None:
        if behind >= _STALE_BEHIND:
            return "stale", f"落后 {behind} 个交易日"
        if behind >= _WARN_BEHIND:
            return "warn", f"落后 {behind} 个交易日"
    elif fallback is not None:
        # 交易日历不可用 → 走独立兜底判据，**绝不能因此判成 ok**
        return fallback
    if gaps.get("suspected"):
        return "warn", f"有 {gaps['suspected']} 天疑似缺口（无日历，需确认）"
    return "ok", "正常"


def _behind_trading_days(session, market: str, latest: date | None,
                         today: date) -> int | None:
    """按**交易日历**算落后几个交易日。日历不可用时返回 None（不猜）。

    ⛔ 不用自然日差 —— 周一早上「上一个交易日是上周五」会被裸算成落后 3 天。
    同一个坑 `daily_pipeline_scheduler._weekday_span` 踩过。

    ## 🔴 陈旧的日历会掩盖陈旧的数据（2026-07-31 实测踩到，别把这段删了）

    港股日线停在 07-27、今天 07-31（落后 3 个交易日），但矩阵里显示 **ok，
    behind=0**。因为拉 `^HSI` 也失败了 → 日历自己也停在 07-27 → 「latest 之后
    还有几个交易日」= 0 → 看起来完全健康。

    **这正是整套设计要消灭的那类病**（同 `_coverage_on` 注释里那句「一个数看着
    没问题，其实什么都没检查」）：判据和被判对象来自同一条坏掉的链路，一起坏
    就一起「正常」。

    修法：日历自己落后于今天太多时**返回 None 而不是 0**，让调用方退回
    `market_freshness.is_stale`（工作日口径、扛得住单个假期）那条独立判据。
    """
    if latest is None:
        return None

    # ⚠️ 判「日历自己停没停更」必须看它的**全局最新日**，不能看查询区间内的最大值。
    # 区间是 `[latest, today]` —— 日历和数据一起卡在 07-27 时，区间内的最大值就是
    # 07-27，看起来「日历是最新的」。**判据和被判对象取自同一个卡住的点，永远自洽。**
    if _calendar_is_stale(session, market, today):
        return None

    from data_engine.trading_calendar import trading_days
    days, confidence = trading_days(session, market, latest, today)
    if confidence != "certain" or not days:
        return None
    # days 含 latest 本身（如果它是交易日），落后数 = 它之后还有几个交易日
    after = [d for d in days if d > latest]
    return len(after)


def _calendar_is_stale(session, market: str, today: date) -> bool:
    """这个市场的交易日历自己是不是停更了。

    判据直接复用 `market_freshness.is_stale`（工作日口径 + 2 天容忍）——
    不新造阈值：日历本来就该每天跟着刷，它的新鲜度标准和行情是同一个。

    长假期间会误判成 stale，那是**安全方向**的误判：结果只是退回独立的兜底判据
    （同样的容忍度），不会凭空报错，也不会把陈旧数据放过去。
    """
    from common.market_freshness import is_stale
    from data_engine.storage.models import TradingCalendar
    from data_engine.trading_calendar import _to_date

    latest_cal = _to_date(
        session.query(func.max(TradingCalendar.cal_date))
        .filter(TradingCalendar.market == market).scalar()
    )
    if latest_cal is None:
        return True
    if is_stale(latest_cal, today, weekend_aware=True):
        logger.warning(
            f"[资产矩阵] {market} 交易日历自己停在 {latest_cal}（今天 {today}），"
            f"不能拿它判落后 —— 退回独立兜底判据"
        )
        return True
    return False


def _fallback_stale(market: str, latest: date | None, today: date) -> tuple[str, str] | None:
    """日历不可用时的兜底判据 —— 复用 `common/market_freshness.is_stale`。

    那是一套独立于交易日历的判据（工作日口径 + 2 天容忍，刻意扛住单个假期），
    所以日历链路整体坏掉时它还站得住。返回 None 表示「按这条判据也没问题」。
    """
    from common.market_freshness import STALE_AFTER_WEEKDAYS, is_stale, weekdays_between
    if latest is None:
        return None
    weekend_aware = market != CRYPTO
    if is_stale(latest, today, weekend_aware=weekend_aware):
        n = weekdays_between(latest, today) if weekend_aware else (today - latest).days
        return "stale", (f"落后约 {n} 个{'工作' if weekend_aware else '自然'}日"
                         f"（交易日历不可用，按 ≥{STALE_AFTER_WEEKDAYS} 天的兜底口径判）")
    return None


def asset_matrix(session) -> dict:
    """完整的资产矩阵快照。"""
    gaps = _gap_counts(session)
    logs = _last_log(session)

    cells: list[dict] = []
    for asset in all_assets():
        market = asset.market or CRYPTO
        today = market_today(market)
        try:
            latest, count, detail = _probe_latest(session, asset)
        except Exception as e:  # noqa: BLE001 — 单个资产探测失败不该让整页 500
            logger.warning(f"[资产矩阵] {asset.key} 探测失败: {e}")
            latest, count, detail = None, None, f"探测失败: {e}"
        # 只有「按交易日组织」的资产才谈得上落后几个交易日。财报按报告期、
        # 行业基本不变、新闻按小时 —— 对它们算「落后几个交易日」没有意义。
        behind = None
        fallback = None
        if asset.gap_probe is not None and asset.market and asset.market != CRYPTO:
            behind = _behind_trading_days(session, market, latest, today)
            if behind is None:
                fallback = _fallback_stale(market, latest, today)
        g = gaps.get(asset.key, {})
        health, reason = _health(asset, latest, g, behind, count, fallback)
        cells.append({
            "key": asset.key,
            "label": asset.label,
            "group": asset.group,
            "market": asset.market,
            "cadence": asset.cadence,
            "enabled": asset.enabled(),
            "in_update_all": asset.in_update_all,
            "supports_gap_fill": asset.supports_gap_fill,
            "hint": asset.hint,
            "latest_date": latest.isoformat() if latest else None,
            "count_at_latest": count,
            "detail": detail,
            "behind_trading_days": behind,
            "gaps_certain": g.get("certain", 0),
            "gaps_suspected": g.get("suspected", 0),
            "health": health,
            "health_reason": reason,
            "last_run": _resolve_log(logs, asset.key),
        })

    worst = "ok"
    for c in cells:
        if c["health"] == "stale":
            worst = "stale"
            break
        if c["health"] == "warn":
            worst = "warn"
    return {
        "cells": cells,
        "overall_health": worst,
        "markets": [A_SHARE, "hk_stock", "us_stock", CRYPTO],
        "groups": ["calendar", "quote", "fundamental", "sentiment", "derived"],
        "server_time": utc_iso(utc_now()),
    }
