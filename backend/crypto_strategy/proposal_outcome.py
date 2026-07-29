"""AI 策略提案的**后验判定** —— 拿真实盈亏给断言打分（S4）。

S2 让每份提案带上「预期收益 / 预期胜率 / 多少天内兑现」，这三个数把提案变成
**可证伪断言**。这个模块把断言真的拿去证伪：

    提案：均线 20→10，预期 30 天 +8%、胜率 55%
      ↓ Jason 批准 → arm → 跑 30 天
    实际：+3%、48%  →  AI 这次判断记一次 miss

⭐ `00-PLAN §3` 裁决 13：**这是直接拿账户余额给 AI 打分**，
比「cockpit 说 BUY 后来涨没涨」跟钱的关系近得多。

# ⛔ 为什么不复用 `common/outcome_eval.py`（裁决 14）

|  | `outcome_eval` | 这里 |
|---|---|---|
| 对象 | 一条建议：symbol + entry_price + 方向 | 一次**参数变更** |
| 窗口 | 5/20 个**交易日** | 提案自己声明的 `horizon_days`（自然日，crypto 7×24） |
| 判定 | 未来价 vs 入场价，含止损/止盈先触发 | 实际收益/胜率 vs **预期**值 |
| 数据源 | 日线 bar | 该策略的**日收益率序列**（S3 建的） |

**没有 symbol、没有 entry_price、没有 bar。** 硬塞进去只会把已经被 P0-4 焊过两遍的
内核再撬开一次。所以独立成模块 + 独立 `ENGINE_VERSION`。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from common.market_time import utc_iso, utc_now

# 改判定口径必须 bump —— 与 `outcome_eval.ENGINE_VERSION` 各管各的。
ENGINE_VERSION = "proposal-outcome-v1"

# 🔴 **容差不能是 1.0**：「预期 +8%，实际 +7.99%」判 miss 是在惩罚精确度而不是判断力。
# 拿到预期的八成算兑现。
DEFAULT_TOLERANCE = 0.8

HIT, MISS, PENDING, UNABLE = "hit", "miss", "pending", "unable"

# 不可重试的 unable：再等多久也不会变（与「窗口没满」那种可重试的分开）。
_TERMINAL_UNABLE = frozenset({"never_armed", "no_applied_version"})


def _tolerant(expected: float, tolerance: float) -> float:
    """带容差的达标线。正预期往下放宽，**负预期往下（更负）放宽**。"""
    return expected * tolerance if expected >= 0 else expected / max(tolerance, 1e-9)


def _win_rate(legs: list[float]) -> float | None:
    """盈利腿占比。没有平仓腿 → None（**不是 0**）。

    返 0 的话，「一次都没平仓」和「平了 10 次全亏」在下游看起来一模一样。
    """
    return (sum(1 for p in legs if p > 0) / len(legs)) if legs else None


def judge(*, expected_return_pct: float, expected_win_rate: float,
          actual_return_pct: float | None, actual_win_rate: float | None,
          window_closed: bool, tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any]:
    """纯判定：给定预期与实际，判 hit/miss/pending/unable。

    ⚠️ **两个指标都要达标才算 hit**。只看收益的话，一条「押对一把大的、其余全亏」的
    改动会被记成成功，而提案里那句「预期胜率 55%」就白写了。
    """
    if not window_closed:
        # ⚠️ `actual` 仍会被写库（供中途查看），但**它是「只走了一半」的读数**，
        # 不是最终成绩。判定本身在这里就短路了，不会拿半截数据下结论。
        return {"outcome": PENDING, "reason": "兑现窗口还没走完",
                "partial": True}
    if actual_return_pct is None or actual_win_rate is None:
        return {"outcome": UNABLE, "unable_reason": "no_realized_data",
                "reason": "窗口走完了，但这条策略在窗口内没有可算的平仓 —— 无从证伪"}
    # ⚠️ **负预期时容差要放松门槛而不是收紧**：预期 −2%、容差 0.8，天真地乘出来是
    # −1.6%，于是「实际 −1.8%（比承诺的还好）」反被判 miss。容差的语义是「差一点也算
    # 兑现」，所以门槛永远要往**对提案更宽容**的方向挪。
    ret_bar = _tolerant(expected_return_pct, tolerance)
    ret_ok = actual_return_pct >= ret_bar
    win_ok = actual_win_rate >= _tolerant(expected_win_rate, tolerance)
    return {
        "outcome": HIT if (ret_ok and win_ok) else MISS,
        "return_ok": ret_ok, "win_rate_ok": win_ok,
        "tolerance": tolerance,
        "return_bar": round(ret_bar, 8),
        "reason": (f"实际 {actual_return_pct:+.2%} / 胜率 {actual_win_rate:.0%}，"
                   f"预期 {expected_return_pct:+.2%} / {expected_win_rate:.0%}"
                   f"（容差 {tolerance:.0%}，收益达标线 {ret_bar:+.2%}）"),
    }


def _actuals(strategy_id: str, since: datetime, until: datetime) -> dict[str, Any]:
    """新版本在窗口内的实际收益与胜率。

    收益走 S3 的日收益率序列（同一把尺子，`daily_returns` 的口径注释一起适用）；
    胜率走平仓腿的盈亏正负。
    """
    from crypto_strategy.performance import daily_returns, strategy_pnl

    # 🔴 **必须传 since/until**，不能只传 `days=窗口跨度`：`daily_returns` 的默认窗口
    # 是贴着「现在」往回数的，而一份 60 天前批准、兑现窗口 30 天的提案要看的是
    # `[T-60, T-30]` —— 传 `days=30` 会取到 `[T-30, T]`，**完全错误的时段且不报错**。
    dr = daily_returns(strategy_id, since=since, until=until)
    if dr.get("basis") == "none":
        return {"return_pct": None, "win_rate": None,
                "reason": dr.get("reason"), "basis": "none"}
    pnl = strategy_pnl(strategy_id, since=since, until=until)
    legs = _closed_leg_pnls(pnl)
    return {
        "return_pct": round(sum(dr.get("returns") or []), 8),
        "win_rate": _win_rate(legs),
        "closed_legs": len(legs),
        "basis": dr.get("basis"),
    }


def _closed_leg_pnls(pnl: dict[str, Any]) -> list[float]:
    """从 `strategy_pnl` 的结果里取每笔平仓的盈亏。

    ⚠️ `basis="mixed"` 时**只取 live 那一半**：paper 是理想撮合，把它掺进「这次改动
    兑现了吗」的判定里，等于拿模拟成绩给真钱决策背书。
    """
    block = (pnl.get("live") or {}) if pnl.get("basis") == "mixed" else pnl
    return list(block.get("closed_leg_pnls") or [])


def evaluate_proposal(proposal_id: str, *, tolerance: float = DEFAULT_TOLERANCE,
                      now: datetime | None = None) -> dict[str, Any]:
    """判一份提案。**不写库**（写库在 `backfill_outcomes`）。"""
    from crypto_strategy import proposals as pr

    p = pr.get_proposal(proposal_id)
    if p is None:
        return {"outcome": UNABLE, "unable_reason": "not_found",
                "reason": f"找不到提案 {proposal_id}"}
    if p["status"] != "applied":
        # 🔴 **「没被批准/没上线」不是「没达到」。** 提案被批准 ≠ 新版本被 arm
        # （S2 刻意把这两件事分开）。一份从没上线的提案**没有可证伪的对象**。
        # 判成 miss 的话，AI 的提案战绩会被一堆「Jason 压根没让它跑」拉低 ——
        # 那不是 AI 的错，把它算进去等于用错误的信号训练自己。
        return {"outcome": UNABLE, "unable_reason": "never_armed",
                "reason": (f"这份提案的状态是 {p['status']}，从没落成上线过的版本 —— "
                           f"没有可证伪的对象，**不算 AI 判断失误**")}
    sid = p.get("applied_strategy_id")
    if not sid:
        return {"outcome": UNABLE, "unable_reason": "no_applied_version",
                "reason": "标成 applied 但没记下新版本号，链路断了"}

    started = _decided_at(p)
    if started is None:
        return {"outcome": UNABLE, "unable_reason": "no_decided_at",
                "reason": "没有批准时间，窗口无从算起"}
    horizon = int(p.get("horizon_days") or 0) or 30
    ends = started + timedelta(days=horizon)
    ref = now or utc_now()
    closed = ref >= ends
    head = {"proposal_id": proposal_id, "applied_strategy_id": sid,
            "window": {"since": utc_iso(started), "until": utc_iso(ends),
                       "horizon_days": horizon, "closed": closed},
            "expected": {"return_pct": p["expected_return_pct"],
                         "win_rate": p["expected_win_rate"]},
            "engine_version": ENGINE_VERSION}

    if not _was_ever_armed(sid):
        # 🔴 **apply ≠ arm**（S2 刻意把这两件事分开）。批准了提案但没让新版本上线，
        # 同样是「没有可证伪的对象」——`status` 此时已经是 applied，光看它分不出来。
        #
        # ⚠️ **但这个判定必须排在窗口判定之后**：`apply_strategy_proposal` 生成的是
        # 草稿、不启用，run 行只由引擎 tick 写（默认 30 分钟一次）。所以「applied 但
        # 一条 run 都没有」是**每一份提案的必经常态**。窗口还没走完就把它写成终态的
        # `never_armed`，回填就再也不会重扫 —— **最新的提案反而是最容易被永久误销号的
        # 那一份**，正好把「拿账户余额给 AI 打分」打成反面。
        if not closed:
            return {**head, "outcome": PENDING,
                    "reason": (f"新版本 {sid} 还没跑起来（引擎按 tick 节奏排单），"
                               f"兑现窗口也还没走完 —— 再等等")}
        return {**head, "outcome": UNABLE, "unable_reason": "never_armed",
                "reason": (f"窗口都走完了，新版本 {sid} 还是一条运行记录都没有 —— "
                           f"提案批准了但没让它跑，**不算 AI 判断失误**")}

    act = _actuals(sid, started, ends if closed else ref)
    r = judge(expected_return_pct=float(p["expected_return_pct"]),
              expected_win_rate=float(p["expected_win_rate"]),
              actual_return_pct=act.get("return_pct"),
              actual_win_rate=act.get("win_rate"),
              window_closed=closed, tolerance=tolerance)
    return {**head, **r, "actual": act, "basis": act.get("basis")}


def _was_ever_armed(strategy_id: str) -> bool:
    """这个版本到底跑过没有 —— 判据是有没有留下运行记录。

    ⚠️ 不看 `status`：策略可能已经被 superseded/retired 了，但它跑过就是跑过。
    ⚠️ paper 的 run 也算「跑过」 —— 竞技场里新版本的正常去处本来就是先进 paper 池
    当挑战者。**paper 与 live 的区别不在这里体现，在 `basis` 上**（见 `_actuals`
    与 `outcome_basis` 列）：那条路是「证据的成色」，这条路只回答「有没有跑」。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyRun
    session = get_session()
    try:
        return bool(session.query(CryptoStrategyRun).filter(
            CryptoStrategyRun.strategy_id == strategy_id).first())
    finally:
        session.close()


def _decided_at(p: dict[str, Any]) -> datetime | None:
    """`to_dict` 把时间序列化成带 offset 的串了，这里转回 naive UTC 好做窗口运算。"""
    raw = p.get("decided_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) - (dt.utcoffset() or timedelta(0)) \
        if dt.tzinfo else dt


def backfill_outcomes(*, limit: int = 50,
                      tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any]:
    """回填所有还没定论的提案。

    ⚠️ 只捞「没评过」和「pending」和**可重试的 unable**。不可重试的
    （`never_armed` / `no_applied_version`）不再重扫 —— 它们等到天荒地老也不会变，
    每天扫一遍纯属浪费（同 `outcome_eval` 的可重试分类思路）。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal

    session = get_session()
    try:
        rows = (session.query(CryptoStrategyProposal)
                .filter(CryptoStrategyProposal.status == "applied")
                .filter((CryptoStrategyProposal.outcome.is_(None))
                        | (CryptoStrategyProposal.outcome == PENDING)
                        | ((CryptoStrategyProposal.outcome == UNABLE)
                           & CryptoStrategyProposal.unable_reason.notin_(
                               tuple(_TERMINAL_UNABLE))))
                .order_by(CryptoStrategyProposal.created_at.asc())
                .limit(max(1, int(limit))).all())
        ids = [r.proposal_id for r in rows]
    finally:
        session.close()

    counts: dict[str, int] = {}
    for pid in ids:
        try:
            r = evaluate_proposal(pid, tolerance=tolerance)
        except Exception as e:  # noqa: BLE001 — 一条评不出来不该掀翻整批
            logger.warning(f"提案后验失败 {pid}: {e}")
            continue
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        _write_back(pid, r)
    return {"scanned": len(ids), "by_outcome": counts,
            "engine_version": ENGINE_VERSION}


def _write_back(proposal_id: str, r: dict[str, Any]) -> None:
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal
    session = get_session()
    try:
        row = session.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.proposal_id == proposal_id).first()
        if row is None:
            return
        act = r.get("actual") or {}
        row.actual_return_pct = act.get("return_pct")
        row.actual_win_rate = act.get("win_rate")
        row.outcome = r["outcome"]
        # 证据成色必须一起落库，否则纸面的 hit 和真钱的 hit 在库里没法区分。
        row.outcome_basis = r.get("basis") or act.get("basis")
        row.unable_reason = r.get("unable_reason")
        row.evaluated_at = utc_now()
        row.engine_version = ENGINE_VERSION
        session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"提案后验回写失败 {proposal_id}: {e}")
    finally:
        session.close()


def scorecard(*, days: int = 365) -> dict[str, Any]:
    """AI 的提案战绩总账。

    🔴 **刻意只报计数，不报「胜率 X%」**（`00-PLAN §4` 问题 2）：
    策略变更一个月 1-2 次 → 一年 12-24 条提案，而 P0-3 的校准门槛是 ≥30 样本。
    「AI 的策略提案准不准」这个数**可能要 2-3 年**才有统计意义。
    现在就摆一个百分比出来，等于给一个纯噪声的数字盖上权威的章。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal

    since = utc_now() - timedelta(days=max(1, int(days)))
    session = get_session()
    try:
        rows = (session.query(CryptoStrategyProposal)
                .filter(CryptoStrategyProposal.created_at >= since).all())
        counts: dict[str, int] = {}
        by_basis: dict[str, int] = {}
        for r in rows:
            key = r.outcome or "unevaluated"
            counts[key] = counts.get(key, 0) + 1
            if key in (HIT, MISS):
                b = r.outcome_basis or "unknown"
                by_basis[f"{key}:{b}"] = by_basis.get(f"{key}:{b}", 0) + 1
        total = len(rows)
    finally:
        session.close()

    decided = counts.get(HIT, 0) + counts.get(MISS, 0)
    real = sum(v for k, v in by_basis.items() if k.endswith(":live_fills"))
    return {
        "window_days": days, "proposals": total, "by_outcome": counts,
        # ⭐ 定论按**证据成色**再分一层：`hit:paper_simulated` 是理想撮合跑出来的，
        # 跟 `hit:live_fills` 不是一回事，合并计数等于拿模拟成绩给真钱决策背书。
        "by_outcome_and_basis": by_basis,
        "decided": decided, "decided_with_real_money": real,
        # ⛔ 别在这里加 `win_rate` / `hit_rate` 字段。加了就一定会有人（包括 LLM）去引用它。
        "note": ("只报计数不报胜率：策略变更一个月 1-2 次，一年 12-24 条提案，"
                 "而统计显著需要 ≥30 样本 —— 这个数要 2-3 年才有意义。"
                 f"当前已定论 {decided} 条（其中真金白银 {real} 条），"
                 f"**样本不足以下任何结论**，把它当定性参考看。"
                 f"另有 {counts.get('unevaluated', 0)} 条还没走到评估"
                 f"（提案中/已拒/被新提案取代）。"),
        "engine_version": ENGINE_VERSION,
    }
