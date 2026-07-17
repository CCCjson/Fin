"""
决策留痕（provenance）—— 把每条 AI 产生的买卖建议持久化，供复现与归因。

设计原则：**record_decision 内部吞掉一切异常**，绝不因留痕失败而弄坏主业务/流式响应。
（advisor 是 SSE 流式，留痕抛异常会掐断用户正在读的回答 —— 这个取舍是刻意的。）

覆盖 advisor / cockpit / moneybill / moneybill_recommend / report_picks 五条 AI 路径；
规则信号已有 Signal 表，不在此重复记录。

后半场（P0-1）：`backfill_outcomes()` 回头看这些建议后来对不对、`get_decision_stats()`
按 source 算胜率。判定内核在 `common/outcome_eval.py`（纯逻辑、DB 无关）。
"""
import json
import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy import case as sql_case

from common.outcome_eval import (
    ENGINE_VERSION,
    MAX_WINDOW,
    RETRYABLE_UNABLE_REASONS,
    OutcomeResult,
    evaluate_single,
)
from data_engine.storage.database import get_session
from data_engine.storage.models import DailyQuote, DecisionLog


def _json(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)


def _cost(model: Optional[str], prompt_tokens: int, completion_tokens: int, total_tokens: int) -> float:
    """复用 agents.usage 的定价；只有 total 时按 50/50 估算。"""
    if not model:
        return 0.0
    try:
        from agents.usage import cost_usd
        if prompt_tokens or completion_tokens:
            return cost_usd(model, prompt_tokens, completion_tokens)
        if total_tokens:
            half = total_tokens // 2
            return cost_usd(model, half, total_tokens - half)
    except Exception:  # noqa: BLE001 — 留痕不可影响主流程
        return 0.0
    return 0.0


def _warn_if_confidence_looks_normalized(source: str, confidence: Optional[float]) -> None:
    """`DecisionLog.confidence` 的量纲是 **0-100**（见 models.py 该列注释）。

    `recommend_engine` 曾经除以 100 存成 0-1，而 `report_picks` 存 0-100 ——
    同一列两个量纲，`get_decision_history` 会把 `0.67` 和 `71.4` 一起端给 MoneyBill，
    跨 source 比胜率必错。写入时就吼一嗓子，别等归因的时候才发现。

    0-1 区间理论上也可能是「真的很低的分」，所以只告警不拦截。
    """
    if confidence is not None and 0 < confidence <= 1:
        logger.warning(
            f"DecisionLog.confidence={confidence} 看着像 0-1 量纲（source={source}）；"
            f"这一列约定是 0-100，写入方是不是多除了个 100？"
        )


def record_decision(
    *,
    source: str,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    action: Optional[str] = None,
    recommendation: Optional[str] = None,
    confidence: Optional[float] = None,
    entry_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    position_pct: Optional[float] = None,
    model_id: Optional[str] = None,
    prompt_version: Optional[str] = None,
    input_snapshot: Any = None,
    reasons: Any = None,
    output_text: Optional[str] = None,
    output_summary: Any = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    turn_start_idx: Optional[int] = None,
    executed: Optional[bool] = None,
    risk_passed: Optional[bool] = None,
) -> Optional[str]:
    """记录一条决策，返回 decision_id；任何异常都被吞掉并返回 None（不影响主流程）。"""
    try:
        _warn_if_confidence_looks_normalized(source, confidence)
        if not total_tokens and (prompt_tokens or completion_tokens):
            total_tokens = prompt_tokens + completion_tokens
        decision_id = uuid.uuid4().hex[:32]
        session = get_session()
        try:
            row = DecisionLog(
                decision_id=decision_id,
                source=source,
                symbol=symbol,
                name=name,
                action=action,
                recommendation=recommendation,
                confidence=confidence,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_pct=position_pct,
                model_id=model_id,
                prompt_version=prompt_version,
                input_snapshot=_json(input_snapshot),
                reasons=_json(reasons),
                output_text=output_text,
                output_summary=_json(output_summary),
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
                total_tokens=total_tokens or 0,
                cost_usd=_cost(model_id, prompt_tokens, completion_tokens, total_tokens),
                latency_ms=latency_ms,
                session_id=session_id,
                turn_start_idx=turn_start_idx,
                executed=(1 if executed else 0) if executed is not None else None,
                risk_passed=(1 if risk_passed else 0) if risk_passed is not None else None,
            )
            session.add(row)
            session.commit()
            return decision_id
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001 — 留痕绝不影响主流程
        logger.warning(f"决策留痕失败（已忽略，不影响主流程）: {e}")
        return None


def _to_dict(r: DecisionLog) -> Dict[str, Any]:
    def _load(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except (TypeError, ValueError):
            return s
    return {
        "id": r.id,
        "decision_id": r.decision_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "source": r.source,
        "symbol": r.symbol,
        "name": r.name,
        "action": r.action,
        "recommendation": r.recommendation,
        "confidence": r.confidence,
        "entry_price": r.entry_price,
        "stop_loss": r.stop_loss,
        "take_profit": r.take_profit,
        "position_pct": r.position_pct,
        "model_id": r.model_id,
        "prompt_version": r.prompt_version,
        "input_snapshot": _load(r.input_snapshot),
        "reasons": _load(r.reasons),
        "output_text": r.output_text,
        "output_summary": _load(r.output_summary),
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.total_tokens,
        "cost_usd": r.cost_usd,
        "latency_ms": r.latency_ms,
        "session_id": r.session_id,
        "turn_start_idx": r.turn_start_idx,
        "executed": r.executed,
        "risk_passed": r.risk_passed,
    }


def query_decisions(
    *,
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """按 symbol/来源/动作/日期过滤，倒序返回决策记录。"""
    session = get_session()
    try:
        q = session.query(DecisionLog)
        if symbol:
            q = q.filter(DecisionLog.symbol == symbol)
        if source:
            q = q.filter(DecisionLog.source == source)
        if action:
            q = q.filter(DecisionLog.action == action)
        if start_date:
            q = q.filter(DecisionLog.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            q = q.filter(DecisionLog.created_at <= datetime.fromisoformat(end_date + " 23:59:59"))
        total = q.count()
        rows = (q.order_by(DecisionLog.created_at.desc())
                .offset(offset).limit(limit).all())
        return {"total": total, "decisions": [_to_dict(r) for r in rows]}
    finally:
        session.close()


def get_decision(decision_id: str) -> Optional[Dict[str, Any]]:
    session = get_session()
    try:
        r = (session.query(DecisionLog)
             .filter(DecisionLog.decision_id == decision_id).first())
        return _to_dict(r) if r else None
    finally:
        session.close()


# ===================== 后验评估回填（P0-1） =====================

# 回填**唯一**允许写的列。白名单而不是黑名单：以后 DecisionLog 加新列，黑名单要人
# 记得同步、忘了就是静默可写；白名单默认拒绝。两个集合的不相交 + 并集覆盖全表由
# tests/baseline/test_decision_outcome_fields.py 钉死。
_OUTCOME_WRITE_FIELDS = frozenset({
    "return_5d", "return_20d", "outcome_5d", "outcome_20d",
    "hit_stop", "hit_target", "first_hit", "first_hit_days",
    "outcome_status", "unable_reason", "engine_version", "evaluated_at",
})

# **原始决策不可篡改** —— 否则复盘就是自欺欺人（改了当时的止损再去算胜率，
# 等于给自己发奖状）。抄自外部蓝本 decision_signal_repo.py:43。
_IMMUTABLE_REFRESH_FIELDS = frozenset({
    "id", "decision_id", "created_at", "source", "symbol", "name",
    "action", "recommendation", "confidence",
    "entry_price", "stop_loss", "take_profit", "position_pct",
    "model_id", "prompt_version", "input_snapshot", "reasons",
    "output_text", "output_summary",
    "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd", "latency_ms",
    "session_id", "turn_start_idx", "executed", "risk_passed",
})


def _apply_outcome(row: DecisionLog, r: OutcomeResult) -> None:
    """把评估结果写回一行 —— 回填的唯一写入口。

    只 setattr 白名单里的列。**别在这儿加「顺便修一下 entry_price」之类的好意**：
    那正是 `_IMMUTABLE_REFRESH_FIELDS` 要挡的事。
    """
    for f in _OUTCOME_WRITE_FIELDS:
        if f == "evaluated_at":
            continue
        setattr(row, f, getattr(r, f, None))
    row.evaluated_at = datetime.now()


def backfill_outcomes() -> Dict[str, int]:
    """回头看所有未终结的建议：后来对了吗？

    **怎么找「到期」的记录 —— 不找。** DecisionLog 只有 `created_at`，硬算到期日
    就要把「20 个交易日」翻译成日历日（不准，且要引交易日历）。改用 SignalTracker
    的状态机：捞出所有未终结的行，让 bar 数自己决定状态。`completed` 和不可重试的
    `unable` 天然被滤掉 → 增量、自限。

    ⚠️ **必须用模块级 `get_session`，不许在函数里重新 import** —— 函数内延迟 import
    会绕过测试的 monkeypatch，那正是「延迟 import 的写库调用把 6 行假推荐写进生产
    market.db」那次事故的成因（见 tests/report/test_report_section_subagents.py:25）。

    同理**不走 `DataEngine.get_daily_data()`** —— 那条路在库里没数据时会自动联网拉。
    直接 ORM 查 DailyQuote 从根上避开（回填不出网）。

    Returns:
        `{"total", "completed", "pending", "unable", "errors"}`。
        「有多少条没法评」（unable）正是这张卡的产出，所以不并进一个 filled 数。
    """
    session = get_session()
    try:
        # 「未终结」= 还没评过 / 评了但窗口没满 / 可重试的 unable。
        # `completed` 和不可重试的 unable 天然落在这个条件外 → 增量、自限。
        unfinished = or_(
            DecisionLog.outcome_status.is_(None),
            DecisionLog.outcome_status == "pending",
            # 可重试的 unable：数据暂缺，明天补了重跑。这个 in_ 就是
            # 「可重试 / 不可重试」二分**唯一**的落地点。
            and_(
                DecisionLog.outcome_status == "unable",
                DecisionLog.unable_reason.in_(sorted(RETRYABLE_UNABLE_REASONS)),
            ),
        )
        rows = session.query(DecisionLog).filter(unfinished).all()

        if not rows:
            logger.info("[决策后验] 没有待评估的建议")
            return {"total": 0, "completed": 0, "pending": 0, "unable": 0, "errors": 0}

        # 批量取行情：一次拉全 + 内存分组，显式避免 N+1。
        # 用 select() 子查询喂 in_()（不是 Python list）—— 绕开 SQLite 变量数上限，
        # 手法与 signal_tracker.py:89-101 逐字同构。**子查询要带上同一个
        # unfinished 条件**，否则会把全表所有 symbol 的行情都拉回来。
        symbols_select = select(distinct(DecisionLog.symbol)).where(
            DecisionLog.symbol.isnot(None), unfinished
        )
        min_date = min(r.created_at.date() for r in rows if r.created_at)
        all_quotes = (
            session.query(DailyQuote)
            .filter(DailyQuote.symbol.in_(symbols_select), DailyQuote.date > min_date)
            .order_by(DailyQuote.symbol.asc(), DailyQuote.date.asc())
            .all()
        )
        quotes_by_symbol: Dict[str, List[DailyQuote]] = defaultdict(list)
        for q in all_quotes:
            quotes_by_symbol[q.symbol].append(q)

        today = date.today()
        stats = {"total": len(rows), "completed": 0, "pending": 0, "unable": 0, "errors": 0}

        for row in rows:
            # 逐条容错：单条炸了不许拖垮整批（抄 validator.py:53-59）
            try:
                created = row.created_at.date() if row.created_at else today
                sym_quotes = quotes_by_symbol.get(row.symbol or "", [])
                # 建议**当天不算**，从次日第一根开始（同 signal_tracker.py:113）。
                # 内存切片 [:20] 直接就是 20 个交易日 —— 免掉日历日换算。
                bars = [q for q in sym_quotes if q.date > created][:MAX_WINDOW]
                result = evaluate_single(row, bars, age_days=(today - created).days)
                _apply_outcome(row, result)
                stats[result.outcome_status] += 1
            except Exception as e:  # noqa: BLE001 — 单条失败不中断整批
                stats["errors"] += 1
                logger.warning(f"[决策后验] 回填 {row.symbol}({row.decision_id}) 失败: {e}")

        session.commit()
        logger.success(
            f"[决策后验] 回填完成: 共 {stats['total']} 条 → "
            f"已评 {stats['completed']} / 待评 {stats['pending']} / "
            f"没法评 {stats['unable']} / 出错 {stats['errors']}"
        )
        return stats
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
