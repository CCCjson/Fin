"""
决策留痕（provenance）—— 把每条 AI 产生的买卖建议持久化，供复现与归因。

设计原则：**record_decision 内部吞掉一切异常**，绝不因留痕失败而弄坏主业务/流式响应。
覆盖 advisor / cockpit / moneybill 三条 AI 路径；规则信号已有 Signal 表，不在此重复记录。
"""
import json
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import DecisionLog


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
