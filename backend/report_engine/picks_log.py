"""买入推荐的留痕与回读 —— 「上次推荐对不对」的闭环。

以前这个闭环是自引用的：全量报告把整份 data 快照塞进 `AnalysisReport.data_snapshot`，
下一期报告再把自己上次写的快照读回来，取里面的 `top_stocks.buy_recommendations`。
全仓只有报告引擎自己读写那张表。

13.2 废掉全量报告后，推荐改落 `DecisionLog`——那张表本来就是为此而建的
（`recommend_engine/engine.py` 的注释原文：「每条 BUY 推荐入 DecisionLog，
供『上次推荐对不对』归因」），且已有 entry_price / stop_loss / take_profit 字段。

**「上期」的语义随之变了**：不再是「上一份同周期的报告」，而是「上一批 report_picks
推荐」。report_picks 现在是用户随时可调的工具，不再是排期产出的报告系列，
按批次回顾比按报告周期回顾更贴合实际。因此这里不按 report_type 过滤。
"""
import json
from datetime import date, datetime, time

from loguru import logger
from sqlalchemy import func

from data_engine.storage.models import DecisionLog
from report_engine.prompt_builder import PROMPT_VERSION as _PROMPT_VERSION

PICKS_SOURCE = "report_picks"


def record_picks(recommendations: list[dict], *, model_id: str = "rule:report_scorer") -> int:
    """把一批买入推荐写进 DecisionLog，返回成功落库的条数。

    `record_decision` 自身吞异常并返回 None，不会打断成稿流程。
    """
    if not recommendations:
        return 0

    from decision_log import record_decision

    written = 0
    for rec in recommendations:
        symbol = rec.get("symbol")
        if not symbol:
            continue
        decision_id = record_decision(
            source=PICKS_SOURCE,
            symbol=symbol,
            name=rec.get("name") or symbol,
            action="BUY",
            recommendation="BUY",
            # 原样存 0-100 的综合评分（不像 recommend_engine 那样除以 100），
            # 回读时要逐字还原成旧的 rec dict 形状。
            confidence=rec.get("composite_score"),
            entry_price=rec.get("price"),
            stop_loss=rec.get("stop_loss"),
            take_profit=rec.get("take_profit"),
            model_id=model_id,
            prompt_version=_PROMPT_VERSION,
            reasons=rec.get("resonance_strategies") or rec.get("strategy") or "",
            output_summary={"composite_score": rec.get("composite_score"),
                            "risk_tier": (rec.get("risk_tier") or {}).get("label")},
        )
        if decision_id:
            written += 1
    logger.info(f"report_picks 留痕: {written}/{len(recommendations)} 条推荐入 DecisionLog")
    return written


def _strategy_text(reasons) -> str:
    """DecisionLog.reasons 存的是 JSON（共振策略列表）或裸字符串。"""
    if not reasons:
        return ""
    if isinstance(reasons, list):
        return ", ".join(str(r) for r in reasons)
    if isinstance(reasons, str):
        try:
            parsed = json.loads(reasons)
        except (json.JSONDecodeError, TypeError):
            return reasons
        if isinstance(parsed, list):
            return ", ".join(str(r) for r in parsed)
        return str(parsed)
    return str(reasons)


def fetch_last_picks(session, before: date) -> tuple[list[dict], date | None]:
    """取 `before` 之前最近一批 report_picks 的买入推荐。

    Returns:
        (recs, batch_date)。recs 逐字还原旧 `top_stocks.buy_recommendations` 里
        回算逻辑用得上的那几个键；没有任何历史推荐时返回 ([], None)。
    """
    cutoff = datetime.combine(before, time.min)
    base = session.query(DecisionLog).filter(
        DecisionLog.source == PICKS_SOURCE,
        DecisionLog.action == "BUY",
        DecisionLog.created_at < cutoff,
    )

    last_ts = base.with_entities(func.max(DecisionLog.created_at)).scalar()
    if not last_ts:
        logger.info("无历史 report_picks 推荐可回顾")
        return [], None

    if isinstance(last_ts, str):          # SQLite 在某些驱动下回字符串
        last_ts = datetime.fromisoformat(last_ts)
    batch_date = last_ts.date()

    rows = base.filter(
        DecisionLog.created_at >= datetime.combine(batch_date, time.min),
        DecisionLog.created_at <= datetime.combine(batch_date, time.max),
    ).order_by(DecisionLog.confidence.desc()).all()

    recs = [{
        "symbol": r.symbol,
        "name": r.name or r.symbol,
        "price": r.entry_price,
        "stop_loss": r.stop_loss,
        "take_profit": r.take_profit,
        "strategy": _strategy_text(r.reasons),
        "composite_score": r.confidence if r.confidence is not None else "",
    } for r in rows]

    logger.info(f"上期推荐回顾: 取到 {batch_date} 那批的 {len(recs)} 只标的")
    return recs, batch_date
