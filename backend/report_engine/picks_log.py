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
from datetime import date, datetime

from loguru import logger
from sqlalchemy import func

from common.market import A_SHARE
from common.market_time import market_day_bounds, market_day_of
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
    # ⛔ `DecisionLog.created_at` 是 `server_default=func.now()` 写的 → SQLite 落 **UTC**，
    # 而原来的边界按**本地**零点构造，差 8 小时。推荐批次是按「北京的哪一天」分组的，
    # 所以日界要按 A 股市场日切、再换算成列的 UTC 口径。
    cutoff = market_day_bounds(A_SHARE, before, storage="utc")[0]
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
    # 同理：`last_ts` 是 UTC 时刻，它属于**北京的哪一天**要用 market_day_of 判，
    # 裸 `.date()` 会把北京 00:00–08:00 出的那批推荐算到前一天去。
    batch_date = market_day_of(last_ts, A_SHARE)
    batch_start, batch_end = market_day_bounds(A_SHARE, batch_date, storage="utc")
    rows = base.filter(
        DecisionLog.created_at >= batch_start,
        DecisionLog.created_at < batch_end,
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
