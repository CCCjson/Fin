"""交易日历真源 —— 判「这一天该不该有数据」。

## 为什么需要它（2026-07-27）

「自动补齐中间空缺的天数」的前提是知道**哪天本该有数据**。此前项目里没有交易
日历，`daily_pipeline_scheduler` 多处注释都明写着「刻意不查节假日」——因为当时
的判据只是「最近一天新不新鲜」，误判代价是几秒空转，扛得住。

判**中间的洞**就完全不同了：没有日历就只能拿「工作日」近似，于是**每一个节假日
都会被报成缺口**。A 股一年 ~11 个法定假日（含调休能到 20+ 天）、港股 ~17 个、
美股 ~10 个且与前两者几乎不重合。误报一次港美股缺口 = 白打 10-15 分钟 Yahoo。

## 三级判据（决定见 docs/16.数据积累与监控/00-PLAN.md §一·决定 2）

1. **基准指数自证（certain）** —— 指数在某天有 bar ⟺ 那天是交易日。
   指数只有 1 只，拉一次几秒钟，**零误报**。本模块负责这一级。
2. **启发式（suspected）** —— 日历拿不到时退回「工作日」。
   ⛔ 只用于展示，**绝不自动补**。本模块的 `weekday_fallback` 负责这一级，
   但它返回的日子调用方必须标记成 `suspected`。
3. **尝试反证（permanent）** —— 补了 N 次仍一行都拉不到的日子认定为假期。
   这一级在 `gap_engine` 里（写 `DataGap.status='permanent'`），本模块不管。

## 为什么放 data_engine 而不是 common

`common/` 的分层约定是**无 DB、无出网**（`market_freshness.py` 就是纯逻辑，DB 查询
留在 `data_engine/health.py`）。日历天然要查库 + 出网，放 common 会破坏那条约定。

## A 股是白嫖的

`000001.SH`（上证指数）**早就在 `daily_quotes` 里**（`stock_type='index'`，由
`DailyUpdater._update_indices_via_pytdx` 每天更新）。所以 A 股日历可以**零网络
请求**直接从存量日线自举，`seed_from_quotes()` 干这件事。

港美股此前**没有基准指数**（`stock_info` 里 hk/us 一条 index 都没有，见
memory `t9-exfat-infra-risk` 记的「缺口=港美股基准指数」），要联网拉。
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from loguru import logger
from sqlalchemy import func

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK
from common.market_time import market_today, utc_now
from data_engine.storage.models import DailyQuote, TradingCalendar

# 各市场的基准指数。选择标准：**一定有数据、一定跟着该市场的交易日历走**。
#
# - A 股用上证指数：库里已有（pytdx 每日更新），零成本。
# - 港股用恒生指数、美股用标普 500：Yahoo 的标准 ticker，`^` 开头是指数惯例。
#   ⚠️ 它们**不入 `daily_quotes`**（会污染覆盖率分子，见 `TradingCalendar` 的
#   docstring），只用来产出日历行。
BENCHMARKS: dict[str, str] = {
    A_SHARE: "000001.SH",
    HK_STOCK: "^HSI",
    US_STOCK: "^GSPC",
}

# 刷新日历时往回拉多少天。365 足够盖住任何现实的缺口窗口，而指数只有 1 只，
# 拉一年 bar 也就一次请求。
CALENDAR_LOOKBACK_DAYS = int(os.getenv("TRADING_CALENDAR_LOOKBACK_DAYS", "365"))

# 日历「够用」的判据：请求区间内至少要有这么多个交易日，才认为日历可信。
# 低于它说明日历本身没建起来（新库/拉取失败），此时必须退回 suspected，
# 否则会把「日历缺行」误当成「行情缺数据」，一次报出几百个假缺口。
_MIN_CALENDAR_DAYS_PER_MONTH = 15

# 区间短于这么多天就不做密度检查（见 `trading_days` 里的说明）。
# 21 天 ≈ 3 周，足够让「每月 15 个交易日」这个下限有意义。
_DENSITY_CHECK_MIN_SPAN_DAYS = 21


def _to_date(value) -> date | None:
    """DailyQuote.date 可能是 date / datetime / str，归一成 date（同 health._to_date）。"""
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


# ---------------------------------------------------------------- 写入侧


def _upsert_days(session, market: str, days: list[date], source: str) -> int:
    """把交易日写进日历表，已存在的跳过。返回新增行数。

    用「先查已有再插差集」而不是 `INSERT OR IGNORE`：日历一年才 ~250 行，
    一次查询的成本可以忽略，换来的是不依赖方言、测试库也一样跑。
    """
    if not days:
        return 0
    existing = {
        d for (d,) in session.query(TradingCalendar.cal_date).filter(
            TradingCalendar.market == market,
            TradingCalendar.cal_date >= min(days),
            TradingCalendar.cal_date <= max(days),
        ).all()
    }
    existing = {_to_date(d) for d in existing}
    fresh = [d for d in days if d not in existing]
    if not fresh:
        return 0
    session.bulk_save_objects([
        TradingCalendar(market=market, cal_date=d, source=source, created_at=utc_now())
        for d in fresh
    ])
    session.commit()
    return len(fresh)


def seed_from_quotes(session, market: str) -> int:
    """从**存量日线**自举日历 —— 零网络请求。

    判据是「基准指数在库里有 bar 的日期」。A 股的 `000001.SH` 本来就每天更新，
    所以 A 股日历可以完全离线建起来。港美股库里没有基准指数 → 返回 0，
    必须走 `refresh_calendar()` 联网拉。

    ⚠️ **不要拿全市场 `distinct date` 来自举** —— 那等于「有数据的那天就是交易日」，
    循环论证：它永远不可能报出缺口（缺的那天自然就不在集合里，于是也不算交易日）。
    必须用一个**独立于待检数据**的信源，这就是基准指数存在的全部意义。
    """
    benchmark = BENCHMARKS.get(market)
    if not benchmark:
        return 0
    rows = session.query(DailyQuote.date).filter(
        DailyQuote.symbol == benchmark,
    ).all()
    days = sorted({d for (d,) in ((_to_date(r[0]),) for r in rows) if d})
    if not days:
        return 0
    added = _upsert_days(session, market, days, source=f"quotes:{benchmark}")
    if added:
        logger.info(f"[交易日历] {market} 从存量日线自举 {added} 个交易日（{benchmark}）")
    return added


def _fetch_overseas_index_days(market: str, start: date, end: date) -> list[date]:
    """联网拉港美股基准指数的交易日。

    ⛔ **不许 `import yfinance`** —— `tests/net/test_egress_single_entry.py` 在 AST
    层面检测，只有 `acquisition`/`net` 顶包豁免。一律走 `acquisition.markets.yf_batch`
    门面（代理已由 `configure_yf_proxy()` 注入）。
    """
    from acquisition.markets.yf_batch import fetch_daily_history

    symbol = BENCHMARKS[market]
    try:
        df = fetch_daily_history(symbol, start.isoformat())
    except Exception as e:  # noqa: BLE001 — 日历拉不到只是降级到 suspected，不能炸
        logger.warning(f"[交易日历] {market} 基准指数 {symbol} 拉取失败: {e}")
        return []
    if df is None or getattr(df, "empty", True):
        logger.warning(f"[交易日历] {market} 基准指数 {symbol} 返回空")
        return []
    out: list[date] = []
    for idx in df.index:
        d = idx.date() if hasattr(idx, "date") else _to_date(idx)
        if d and start <= d <= end:
            out.append(d)
    return sorted(set(out))


def refresh_calendar(session, market: str, *, today: date | None = None) -> dict:
    """刷新一个市场的交易日历。

    A 股：先从存量日线自举（免费），够新就不出网。
    港美股：联网拉基准指数。
    crypto：不入表（自然日，纯计算），直接返回。

    Returns:
        `{"market", "added", "source", "latest"}`；`added` 是新增的交易日行数。
    """
    today = today or market_today(market)
    if market == CRYPTO:
        return {"market": market, "added": 0, "source": "natural_days",
                "latest": today.isoformat()}

    # 先自举（A 股这一步就够了；港美股库里没基准指数，返回 0）
    added = seed_from_quotes(session, market)

    latest = session.query(func.max(TradingCalendar.cal_date)).filter(
        TradingCalendar.market == market,
    ).scalar()
    latest = _to_date(latest)

    # 自举后日历已经追到最近 3 天内 → 不必再出网（A 股的常态路径）
    if latest and (today - latest).days <= 3:
        return {"market": market, "added": added, "source": "quotes",
                "latest": latest.isoformat()}

    if market not in (HK_STOCK, US_STOCK):
        # A 股自举都追不上（说明指数日线本身断了）——那是 daily 资产的问题，
        # 不在日历层解决，如实返回让上层判 suspected
        return {"market": market, "added": added, "source": "quotes",
                "latest": latest.isoformat() if latest else None}

    start = today - timedelta(days=CALENDAR_LOOKBACK_DAYS)
    days = _fetch_overseas_index_days(market, start, today)
    if days:
        added += _upsert_days(session, market, days, source=f"index:{BENCHMARKS[market]}")
        logger.info(f"[交易日历] {market} 联网补 {len(days)} 个交易日"
                    f"（{BENCHMARKS[market]}，新增 {added} 行）")
        latest = max(days)
    return {"market": market, "added": added, "source": f"index:{BENCHMARKS[market]}",
            "latest": latest.isoformat() if latest else None}


# ---------------------------------------------------------------- 读取侧


def natural_days(start: date, end: date) -> list[date]:
    """`[start, end]` 闭区间的全部自然日（crypto 7×24 用）。"""
    if end < start:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def weekday_fallback(start: date, end: date) -> list[date]:
    """启发式：`[start, end]` 闭区间的全部工作日。

    ⛔ **调用方必须把结果标成 `suspected`，绝不能拿它自动触发补齐。**
    它不认识任何节假日，A 股国庆一次就能报出 7 个假缺口。
    """
    return [d for d in natural_days(start, end) if d.weekday() < 5]


def trading_days(session, market: str, start: date, end: date) -> tuple[list[date], str]:
    """`[start, end]` 闭区间内这个市场的交易日。

    Returns:
        `(days, confidence)` —— `confidence` 是 `"certain"`（日历可信）或
        `"suspected"`（日历不可用，退回了工作日启发式）。
        **调用方必须看 confidence**：`suspected` 的缺口只许展示不许自动补。

    ## 「日历只盖住半个窗口」怎么办：只返回它盖住的那段

    港美股的日历是拉一年（`CALENDAR_LOOKBACK_DAYS`），A 股是从 2010 年自举的 ——
    两边覆盖范围差得很远，而调用方给的窗口是统一的。

    ⛔ **绝不能把「日历没盖到的那段」当成「那段没有交易日」** —— 那会让缺口扫描
    对那一段完全失明（更糟的是它看起来还很正常）。也不能因此整体退回启发式 ——
    日历盖住的那部分本来是可信的，白白降级成 suspected 就补不了了。

    所以：**收窄到日历实际盖住的子区间**，在那段里给 certain。剩下的段落既不
    宣称有交易日、也不宣称没有，交给调用方（缺口扫描据此少扫一段，不会误报）。

    crypto 恒为 `certain`（7×24 自然日，不需要日历）。
    """
    if end < start:
        return [], "certain"
    if market == CRYPTO:
        return natural_days(start, end), "certain"

    rows = session.query(TradingCalendar.cal_date).filter(
        TradingCalendar.market == market,
        TradingCalendar.cal_date >= start,
        TradingCalendar.cal_date <= end,
    ).all()
    days = sorted({d for (d,) in ((_to_date(r[0]),) for r in rows) if d})

    if not days:
        logger.warning(
            f"[交易日历] {market} {start}~{end} 一个交易日都没有 → 退回工作日启发式"
            f"（结果标 suspected，不会自动补齐）"
        )
        return weekday_fallback(start, end), "suspected"

    # 密度检查只在**日历实际盖住的子区间**上做。低于下限说明日历本身是残的
    # （拉了一半 / 自举中断），此时那段里的「空缺」很可能是日历缺行而非行情缺数据，
    # 必须降级，否则会一口气报出几百个假缺口并触发补齐。
    #
    # ⚠️ **短区间不做密度检查**：问「最近 3 天有几个交易日」时，正确答案本来就
    # 可能只有 1~2 个，套「每月 ≥15 个」的下限必然不达标 → 一律降级 suspected。
    # 实测踩到：`asset_status._behind_trading_days` 拿 `[latest, today]` 来问，
    # 区间常常只有一天，于是「落后几个交易日」永远算不出来（全是 None）。
    covered_start, covered_end = days[0], days[-1]
    covered_span = (covered_end - covered_start).days
    span_months = max(covered_span / 30.0, 1.0)
    if covered_span >= _DENSITY_CHECK_MIN_SPAN_DAYS \
            and len(days) < _MIN_CALENDAR_DAYS_PER_MONTH * span_months:
        logger.warning(
            f"[交易日历] {market} {covered_start}~{covered_end} 只有 {len(days)} 个"
            f"交易日，密度低于下限 → 判定日历残缺，退回工作日启发式（标 suspected）"
        )
        return weekday_fallback(start, end), "suspected"

    if covered_start > start:
        logger.debug(
            f"[交易日历] {market} 日历只盖到 {covered_start} 起，"
            f"{start}~{covered_start} 这段不参与缺口判定（不宣称有也不宣称没有）"
        )
    return days, "certain"


def calendar_status(session) -> dict:
    """各市场日历的建设情况（供监控面板展示 / 排障）。"""
    out: dict = {}
    for market in (A_SHARE, HK_STOCK, US_STOCK):
        row = session.query(
            func.count(TradingCalendar.id),
            func.min(TradingCalendar.cal_date),
            func.max(TradingCalendar.cal_date),
        ).filter(TradingCalendar.market == market).first()
        count, first, last = row if row else (0, None, None)
        out[market] = {
            "benchmark": BENCHMARKS.get(market),
            "days": int(count or 0),
            "first_date": _to_date(first).isoformat() if first else None,
            "latest_date": _to_date(last).isoformat() if last else None,
        }
    out[CRYPTO] = {"benchmark": None, "days": None, "first_date": None,
                   "latest_date": None, "note": "7×24 自然日，不需要日历表"}
    return out
