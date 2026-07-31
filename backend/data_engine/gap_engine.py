"""缺口引擎 —— 扫出「这一天本该有数据但没有」，并把它补回来。

## 这是本次重构的核心新能力（2026-07-27）

现有的 `daily_pipeline_scheduler._catchup_job` 只管**尾部落后**：看「最近一个该有
数据的交易日」覆盖率够不够。它对**中间的洞**结构性失明 —— 07-10~07-15 全空、
07-16 跑了一次，覆盖率就满分了，那 4 个交易日永久是洞，而且无声无息。

## 判据：三级

    1. certain    基准指数自证的交易日 − 实际有数据的日  →  自动补
    2. suspected  没有日历、只靠「工作日」推断出来的      →  ⛔ 只报不补
    3. permanent  补了 MAX_ATTEMPTS 次仍拉不到数据        →  认定假期，不再重试

**第 3 级是这套设计的关键**。项目里没有第三方交易日历，`permanent` 就是用尝试
结果反证出来的假期表 —— 存进 `data_gaps` 后，同一个补不上的洞不会每次启动都重试。
没有它，港美股每次启动都要白打 10-15 分钟 Yahoo。

## 「残缺」也算缺口

不是只有整天空才算。当日覆盖数低于近期中位数基线的 `QUALIFIED_RATIO`（0.8）就算 ——
实测现场 `us_stock` 的 2026-07-03 有 5 行（正常日 11000+），只看「有没有行」会
判它健康。判据复用 `common/market_freshness.py` 那套中位数基线（自校准，universe
变了不用回来改常量）。

⛔ **绝不能用 `max(date)` 判**，见 `daily_pipeline_scheduler._coverage_on` 的注释：
全市场只要有 1 只票领先，max 就是最新日期，「一个数看着没问题，其实什么都没检查」。
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from loguru import logger

from common.db import commit_with_retry
from common.market import CRYPTO
from common.market_freshness import BASELINE_DAYS, QUALIFIED_RATIO
from common.market_time import market_today, utc_now
from data_engine.registry import RunContext, assets_for, get_asset
from data_engine.storage.database import get_session
from data_engine.storage.models import DataGap

# 扫多少天以内的缺口。90 天 ≈ 60 个交易日，够盖任何现实的停机窗口；
# 再往前是深历史回补的活，不该让每日扫描去操心。
SCAN_LOOKBACK_DAYS = int(os.getenv("GAP_SCAN_LOOKBACK_DAYS", "90"))

# 补几次仍拉不到就认定为假期/源无数据。
# 取 3：一次可能是网络抖动，两次可能是限速，三次基本可以断定那天真的没有数据。
MAX_ATTEMPTS = int(os.getenv("GAP_MAX_ATTEMPTS", "3"))

# 最近 N 个**交易日**不判缺口 —— 「今天/昨天还没跑」不是缺口，是还没到点。
# 这个缓冲交给现有的 `_catchup_job`（它专管尾部），两者分工不重叠。
#
# ⚠️ 单位必须是**交易日**不是自然日。原来写的自然日，周一跑的时候「最近 2 个
# 自然日」= 周六周日，一个交易日都没挡住，于是上周五那根还没落库就被报成缺口。
# 同一个坑 `daily_pipeline_scheduler._weekday_span` 也踩过（周五→周一被裸算成
# 落后 3 天，误触发一次 10-15 分钟的空拉）。
TAIL_BUFFER_TRADING_DAYS = int(os.getenv("GAP_TAIL_BUFFER_TRADING_DAYS", "2"))


def _baseline(counts: list[int]) -> float:
    """近期覆盖数的中位数基线。口径同 `market_freshness`（相对阈值、自校准）。

    为什么是中位数而不是绝对阈值：各市场的稳态覆盖率差异极大（A股 99.5%、
    港股 62.8%、美股 45%，因为 `is_active` 分母本身虚高），任何跨市场的绝对
    阈值都会让美股永远不达标。见 `common/market_freshness.py` 顶部的实测数据。
    """
    if not counts:
        return 0.0
    top = sorted(counts, reverse=True)[:BASELINE_DAYS]
    ordered = sorted(top)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _threshold_for(asset, baseline: float) -> float:
    """某个资产判「这天算缺」的门槛。

    两种口径，**不能混用**：

    - `coverage`（日线类）：当日覆盖数 < 基线 × 0.8 算缺。日线每天该有的票数是
      稳定的，低于基线就是跑残了。实测 `us_stock` 2026-07-03 只有 5 行
      （正常 11000+）就是这么逮到的。
    - `presence`（涨停池类）：只要 > 0 就算有。涨停家数本身天天剧烈波动
      （行情差的日子 30 家、好的日子 100+ 家），拿 0.8×中位数去卡它会把每个
      弱势日都误报成缺口。这类资产只该问「那天到底跑没跑」。
    """
    if asset.gap_mode == "presence":
        return 1.0
    return baseline * QUALIFIED_RATIO


# ====================================================================
# 扫描
# ====================================================================


def scan_asset(session, asset_key: str, *, today: date | None = None,
               lookback_days: int | None = None) -> dict:
    """扫一个资产的缺口，落 `data_gaps` 表。

    Returns:
        `{"asset","market","confidence","scanned_days","gaps":[...],"new","reopened"}`
    """
    asset = get_asset(asset_key)
    if asset.gap_probe is None:
        return {"asset": asset_key, "error": "该资产不支持缺口扫描"}

    from data_engine.trading_calendar import trading_days

    market = asset.market or CRYPTO
    today = today or market_today(market)
    lookback = lookback_days or SCAN_LOOKBACK_DAYS
    start = today - timedelta(days=lookback)
    expected, confidence = trading_days(session, market, start, today)

    # 尾部缓冲：最近几个**交易日**归 _catchup_job 管，这里不重复报
    if TAIL_BUFFER_TRADING_DAYS > 0:
        expected = expected[:-TAIL_BUFFER_TRADING_DAYS] or []
    if not expected:
        return {"asset": asset_key, "market": market, "confidence": confidence,
                "scanned_days": 0, "gaps": [], "new": 0, "reopened": 0}
    end = expected[-1]

    observed = asset.gap_probe(session, start, end)
    baseline = _baseline([c for c in observed.values() if c > 0])
    threshold = _threshold_for(asset, baseline)

    # 基线为 0 = 这个资产整段区间一行都没有。**不报缺口** —— 那不是「缺了几天」，
    # 是这个资产压根没建起来（新库/未启用），报出来会是几十条噪音，而且自动补齐
    # 会去拉全历史。这种情况该走深历史回补，不是补洞。
    if baseline <= 0:
        logger.info(f"[缺口扫描] {asset_key} 区间内无任何数据，跳过（该走深历史回补）")
        return {"asset": asset_key, "market": market, "confidence": confidence,
                "scanned_days": len(expected), "gaps": [], "new": 0, "reopened": 0,
                "note": "该资产在扫描区间内无数据，跳过"}

    # ⛔ **不报「这个资产开始积累之前」的日子**。
    #
    # 实测踩到：`limit_up.a_share` 表里只有 2026-07-03 起的 10 天数据（涨停池功能
    # 上线的日子），90 天窗口一扫就报出 51 个「缺口」——其中 44 个根本不是缺口，
    # 是**功能上线前**。自动补齐会去打 44 次东财、全部空手而归，然后 3 轮之后
    # 集体标 permanent，纯属浪费。
    #
    # 判据用「窗口内第一个有数据的日子」：项目里没有「资产上线时间」这种元数据，
    # 而这是能用现有信息做出的最保守推断。代价是窗口最左端的真缺口会被漏掉一次 ——
    # 可以接受：那种情况属于「断更很久」，归尾部补跑（`_catchup_job`）和肉眼可见的
    # `latest_date` 管，不是这里的职责。
    first_with_data = min((d for d, c in observed.items() if c > 0), default=None)
    if first_with_data is None:
        return {"asset": asset_key, "market": market, "confidence": confidence,
                "scanned_days": len(expected), "gaps": [], "new": 0, "reopened": 0}
    scoped = [d for d in expected if d >= first_with_data]
    if len(scoped) < len(expected):
        logger.debug(
            f"[缺口扫描] {asset_key} 窗口收窄到 {first_with_data} 起"
            f"（之前 {len(expected) - len(scoped)} 天该资产尚未开始积累，不算缺口）"
        )

    missing: list[tuple[date, int]] = []
    for d in scoped:
        count = observed.get(d, 0)
        if count < threshold:
            missing.append((d, count))

    new_count, reopened = _persist_gaps(
        session, asset_key, market, missing, confidence, int(baseline),
        scope=(first_with_data, end),
    )
    if missing:
        logger.warning(
            f"[缺口扫描] {asset_key} {start}~{end}：{len(missing)} 个缺口"
            f"（{confidence}，基线 {int(baseline)}，阈值 {int(threshold)}）"
            f"→ {[d.isoformat() for d, _ in missing[:10]]}"
        )
    return {
        "asset": asset_key, "market": market, "confidence": confidence,
        "scanned_days": len(scoped), "baseline": int(baseline),
        "since": first_with_data.isoformat(),
        "gaps": [{"date": d.isoformat(), "observed": c} for d, c in missing],
        "new": new_count, "reopened": reopened,
    }


def _persist_gaps(session, asset_key: str, market: str,
                  missing: list[tuple[date, int]], confidence: str,
                  expected_count: int, *,
                  scope: tuple[date, date]) -> tuple[int, int]:
    """把扫出来的缺口写进表。

    - 已经是 `permanent` 的**不动**（那是反证出来的假期，重开就等于白重试）
    - 已经是 `filled` 但又缺了 → 重开为 `open`（数据被删/回滚了，重新补）
    - `suspected` 与 `certain` 之间会互相升降级：日历后来建起来了，
      原本 suspected 的缺口应该转成 certain 才可能被自动补
    - **不再缺的自动收口成 filled** —— 否则手动补过之后表里永远挂着红点
    - **落在本次扫描范围之外的非 permanent 行直接删掉** —— 判据变了（窗口收窄、
      日历建起来了）之后，旧结论不该继续挂在缺口区里骗人。permanent 是实测反证
      出来的假期，那个必须留着，否则每次启动都要重试它。
    """
    now = utc_now()
    missing_map = {d: c for d, c in missing}
    scope_start, scope_end = scope

    existing = {
        g.gap_date: g for g in session.query(DataGap).filter(
            DataGap.asset == asset_key,
        ).all()
    }

    new_count = reopened = 0
    for d, observed in missing:
        row = existing.get(d)
        if row is None:
            session.add(DataGap(
                asset=asset_key, market=market, gap_date=d,
                status="open" if confidence == "certain" else "suspected",
                confidence=confidence, attempts=0,
                observed_count=observed, expected_count=expected_count,
                detected_at=now,
            ))
            new_count += 1
            continue
        if row.status == "permanent":
            continue        # 反证出来的假期，别再折腾它
        row.observed_count = observed
        row.expected_count = expected_count
        if row.confidence != confidence:
            row.confidence = confidence
            # 日历建起来了：suspected → certain，让它变得可自动补
            if confidence == "certain" and row.status == "suspected":
                row.status = "open"
        if row.status == "filled":
            row.status = "open" if confidence == "certain" else "suspected"
            row.filled_at = None
            reopened += 1

    for d, row in existing.items():
        if d in missing_map or row.status == "permanent":
            continue
        if d < scope_start or d > scope_end:
            # 判据变了（窗口收窄 / 日历建起来了），旧结论作废
            session.delete(row)
            continue
        if row.status != "filled":
            # 这次扫描已经不缺了 → 收口
            row.status = "filled"
            row.filled_at = now

    commit_with_retry(session, label="缺口扫描落库")
    return new_count, reopened


def scan_all(session, *, today: date | None = None,
             lookback_days: int | None = None) -> dict:
    """扫全部支持补洞的资产。

    **扫描前先刷新交易日历** —— 没有日历一切都会退回 suspected，扫了也不能自动补。
    A 股是从存量日线自举的（零网络请求），港美股要联网拉一次指数（几秒钟）。
    """
    from data_engine.trading_calendar import refresh_calendar

    results: dict = {"calendars": {}, "assets": {}, "total_gaps": 0, "fillable": 0}
    for market in ("a_share", "hk_stock", "us_stock"):
        try:
            results["calendars"][market] = refresh_calendar(session, market, today=today)
        except Exception as e:  # noqa: BLE001 — 日历拉不到只降级，不中断扫描
            logger.warning(f"[缺口扫描] {market} 日历刷新失败: {e}")
            results["calendars"][market] = {"error": str(e)}

    for asset in assets_for(gap_fillable=True):
        try:
            r = scan_asset(session, asset.key, today=today, lookback_days=lookback_days)
        except Exception as e:  # noqa: BLE001 — 一个资产扫挂了不影响其余
            logger.warning(f"[缺口扫描] {asset.key} 异常: {e}")
            r = {"asset": asset.key, "error": str(e)}
        results["assets"][asset.key] = r
        results["total_gaps"] += len(r.get("gaps") or [])
        if r.get("confidence") == "certain":
            results["fillable"] += len(r.get("gaps") or [])
    logger.info(f"[缺口扫描] 完成：共 {results['total_gaps']} 个缺口，"
                f"其中 {results['fillable']} 个可自动补")
    return results


# ====================================================================
# 查询
# ====================================================================


def list_gaps(session, *, status: str | None = None, market: str | None = None,
              limit: int = 200) -> list[dict]:
    """缺口列表（供前端缺口区 / API）。默认按日期倒序。"""
    q = session.query(DataGap)
    if status:
        q = q.filter(DataGap.status == status)
    elif status is None:
        # 默认不返回已收口的，前端红区只关心还缺着的
        q = q.filter(DataGap.status.in_(("open", "filling", "suspected")))
    if market:
        q = q.filter(DataGap.market == market)
    rows = q.order_by(DataGap.gap_date.desc()).limit(limit).all()
    return [
        {
            "id": g.id, "asset": g.asset, "market": g.market,
            "date": g.gap_date.isoformat() if g.gap_date else None,
            "status": g.status, "confidence": g.confidence,
            "attempts": g.attempts,
            "observed_count": g.observed_count, "expected_count": g.expected_count,
            "note": g.note,
        }
        for g in rows
    ]


def gap_summary(session) -> dict:
    """按资产聚合的缺口摘要（供总控条的「缺口 N 天」角标）。"""
    gaps = list_gaps(session, limit=1000)
    by_asset: dict[str, dict] = {}
    for g in gaps:
        slot = by_asset.setdefault(g["asset"], {
            "asset": g["asset"], "market": g["market"],
            "certain": 0, "suspected": 0, "dates": [],
        })
        if g["confidence"] == "certain":
            slot["certain"] += 1
        else:
            slot["suspected"] += 1
        slot["dates"].append(g["date"])
    for slot in by_asset.values():
        slot["dates"] = sorted(d for d in slot["dates"] if d)
    return {
        "total": len(gaps),
        "fillable": sum(s["certain"] for s in by_asset.values()),
        "by_asset": list(by_asset.values()),
    }


# ====================================================================
# 补齐
# ====================================================================


def fillable_gaps(session, *, asset_key: str | None = None) -> dict[str, list[date]]:
    """哪些缺口可以自动补 → `{asset_key: [日期, ...]}`。

    只收 `certain` + 未超尝试上限的。⛔ `suspected` 永远不进这里 —— 它可能是
    节假日，自动补一次港美股要白打 10-15 分钟 Yahoo。
    """
    q = session.query(DataGap).filter(
        DataGap.status.in_(("open", "filling")),
        DataGap.confidence == "certain",
        DataGap.attempts < MAX_ATTEMPTS,
    )
    if asset_key:
        q = q.filter(DataGap.asset == asset_key)
    out: dict[str, list[date]] = {}
    for g in q.order_by(DataGap.gap_date).all():
        out.setdefault(g.asset, []).append(g.gap_date)
    return out


def fill_asset_gaps(asset_key: str, gap_dates: list[date], *,
                    should_stop=lambda: False) -> dict:
    """补一个资产的一组缺口，跑完后**回查**是否真补上了，据此更新缺口状态。

    「回查」这一步不能省：runner 报 `complete` 只说明请求跑完了，不代表那几天
    真的落库了（东财/Yahoo 对非交易日会正常返回空）。**必须拿探测器再数一遍**，
    否则假期会永远停在 `open` 反复重试 —— `permanent` 的反证正是靠这一步。
    """
    asset = get_asset(asset_key)
    if not asset.supports_gap_fill:
        return {"asset": asset_key, "error": "该资产不支持补洞"}
    if not gap_dates:
        return {"asset": asset_key, "filled": 0, "note": "没有要补的日期"}

    ctx = RunContext(mode="gap_fill", gap_dates=tuple(sorted(gap_dates)),
                     should_stop=should_stop)

    session = get_session()
    try:
        _mark(session, asset_key, gap_dates, status="filling", bump_attempt=True)
    finally:
        session.close()

    events: list[dict] = []
    error: str | None = None
    try:
        for evt in asset.runner(ctx):
            events.append(evt)
    except Exception as e:  # noqa: BLE001 — 补洞失败不能掀翻调用它的 job
        error = str(e)
        logger.exception(f"[补洞] {asset_key} 执行异常: {e}")

    # 回查：这几天现在到底有没有数据
    session = get_session()
    try:
        # 🔴 **基线必须用完整扫描窗口算，绝不能只探缺口那几天**（2026-07-31 实测踩到）
        #
        # 原来这里是 `gap_probe(min(gap_dates), max(gap_dates))` —— 只看缺口区间。
        # 而那个区间**恰恰就是数据不全的区间**，于是中位数基线被缺口自己拉低：
        #
        #     扫描时  90 天窗口 → baseline=11324, 阈值 9059 → 5343 < 9059 → 报缺口 ✓
        #     回查时  只看那 2 天 → baseline= 5339, 阈值 4271 → 5343 > 4271 → 判已补 ✗
        #
        # 两个后果，都很难看：
        #   1. **无限循环**：下次扫描（完整窗口）又报同样 2 天 → 补 8 分钟 → 又判
        #      filled → 每 6 小时白跑一次，而且日志上看着一切正常
        #   2. **`permanent` 反证永远失效**：判 filled 就不累加 attempts，那条
        #      「补 N 次仍无数据就认定假期」的兜底线路根本走不到
        #
        # 与「陈旧日历掩盖陈旧数据」是同一类病：**判据取自被判对象自身**。
        today = market_today(asset.market or CRYPTO)
        probe_start = min(min(gap_dates), today - timedelta(days=SCAN_LOOKBACK_DAYS))
        observed = asset.gap_probe(session, probe_start, today)
        baseline = _baseline([c for c in observed.values() if c > 0])
        threshold = _threshold_for(asset, baseline) if baseline > 0 else 1.0
        filled, still_missing, permanent = [], [], []
        for d in gap_dates:
            if observed.get(d, 0) >= threshold:
                filled.append(d)
            else:
                still_missing.append(d)
        _mark(session, asset_key, filled, status="filled")
        # 尝试次数到顶还是**一条都没有** → 反证为假期，标 permanent 不再重试
        #
        # 🔴 **`observed == 0` 这个条件不能去掉**（2026-07-31 实测加的）。
        #
        # `permanent` 的语义是「数据源那天根本没有数据」= 那天不是交易日。但
        # 「数据源限速导致只拉到一半」会走到**同一个终点**：都是补了 N 次仍不达标。
        # 两者必须分开，否则限速期间的日子会被永久标成假期，**再也不会被补**。
        #
        # 判据很干净：**假期不可能有任何 bar**。实测现场美股 07-27 有 5343 只票
        # 有数据（正常 11000+）—— 有 5343 条就证明那天是交易日，只是没拉全，
        # 该继续重试（等 Yahoo 限速过去 / 换个网络），绝不能销号。
        for d in still_missing:
            row = _get_row(session, asset_key, d)
            if row is None:
                continue
            seen = observed.get(d, 0)
            if row.attempts >= MAX_ATTEMPTS and seen == 0:
                row.status = "permanent"
                row.note = (f"补齐 {row.attempts} 次仍**一条数据都没有**，认定为"
                            f"非交易日/数据源无此日（实测反证，不再重试）")
                permanent.append(d)
            else:
                row.status = "open"
                if seen:
                    row.note = (f"补了 {row.attempts} 次仍只有 {seen} 条"
                                f"（阈值 {int(threshold)}）。**有数据说明那天是交易日**，"
                                f"多半是数据源限速/拉取不全 —— 保持待补，不销号")
        commit_with_retry(session, label="缺口回查")
    finally:
        session.close()

    from data_engine.events import summarize
    out = {
        "asset": asset_key,
        "requested": [d.isoformat() for d in gap_dates],
        "filled": [d.isoformat() for d in filled],
        "still_missing": [d.isoformat() for d in still_missing],
        "marked_permanent": [d.isoformat() for d in permanent],
        "summary": summarize(events),
    }
    if error:
        out["error"] = error
    logger.info(f"[补洞] {asset_key} 结果: 补上 {len(filled)} 天，"
                f"仍缺 {len(still_missing)} 天，认定假期 {len(permanent)} 天")
    return out


def _get_row(session, asset_key: str, d: date) -> DataGap | None:
    return session.query(DataGap).filter(
        DataGap.asset == asset_key, DataGap.gap_date == d,
    ).first()


def _mark(session, asset_key: str, days: list[date], *, status: str,
          bump_attempt: bool = False) -> None:
    if not days:
        return
    now = utc_now()
    for d in days:
        row = _get_row(session, asset_key, d)
        if row is None:
            continue
        row.status = status
        if bump_attempt:
            row.attempts = (row.attempts or 0) + 1
            row.last_attempt_at = now
        if status == "filled":
            row.filled_at = now
    commit_with_retry(session, label=f"缺口标记 {status}")


def fill_all(*, should_stop=lambda: False, asset_key: str | None = None) -> dict:
    """扫 + 补一条龙 —— 缺口 job 的主体。

    ⛔ **只补 `certain`**。`suspected` 的缺口留在表里给前端展示，等日历建起来后
    下一次扫描会自动把它们升级成 `certain`，那时才会被补。
    """
    session = get_session()
    try:
        scan = scan_all(session)
        todo = fillable_gaps(session, asset_key=asset_key)
    finally:
        session.close()

    results: list[dict] = []
    for key, days in todo.items():
        if should_stop():
            logger.info("[补洞] 收到停止信号，剩余资产不再处理")
            break
        results.append(fill_asset_gaps(key, days, should_stop=should_stop))
    return {"scan": scan, "fills": results}
