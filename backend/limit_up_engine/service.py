"""
涨停引擎对外统一入口 —— 工具层/API层都调这里，不直接碰 ORM。

- run_daily_prediction(): 盘后批处理主流程（抓池子→算情绪→筛候选→打分→落库），
  供 daily_pipeline_scheduler 调用。
- get_pool_overview(): 涨停池复盘查询，供 get_limit_up_pool 工具调用。
- get_candidates(): 候选池打分排名查询，供 predict_limit_up_candidates 工具调用。
"""
import json
from datetime import datetime, date as date_cls, timedelta
from typing import Dict, List, Optional

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import LimitUpPool, LimitUpPrediction
from limit_up_engine import metrics, candidate_pool, scoring
from limit_up_engine.ingest import ingest_trade_date


def _next_trading_day(d: date_cls) -> date_cls:
    """简单跳过周末，不处理节假日（MVP 已知局限，见方案风险点）。"""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def run_daily_prediction(trade_date_str: Optional[str] = None) -> Dict:
    """盘后主流程：拉池子落库 → 算情绪指标 → 筛候选 → 打分 → 落 LimitUpPrediction。"""
    if trade_date_str is None:
        trade_date_str = datetime.now().strftime("%Y%m%d")
    trade_date = datetime.strptime(trade_date_str, "%Y%m%d").date()
    target_date = _next_trading_day(trade_date)

    ingest_summary = ingest_trade_date(trade_date_str)

    session = get_session()
    try:
        market_sentiment = metrics.get_market_sentiment(session, trade_date)
        industry_heat = metrics.get_industry_heat(session, trade_date)
        candidates = candidate_pool.build_candidate_pool(session, trade_date, trade_date_str)
        scored = scoring.score_candidates(candidates, market_sentiment, industry_heat)

        session.query(LimitUpPrediction).filter(
            LimitUpPrediction.predict_date == trade_date,
        ).delete()

        for item in scored:
            session.add(LimitUpPrediction(
                predict_date=trade_date,
                target_date=target_date,
                symbol=item["symbol"],
                name=item["name"],
                score=item["score"],
                rank=item["rank"],
                source=item["source"],
                momentum_score=item["momentum_score"],
                capital_score=item["capital_score"],
                theme_score=item["theme_score"],
                sentiment_score=item["sentiment_score"],
                continuation_score=item["continuation_score"],
                reasons=json.dumps(item["reasons"], ensure_ascii=False),
                model_version="rule_v1",
            ))
        session.commit()
        logger.info(f"[涨停预测] {trade_date_str} → {target_date} 候选打分完成，共 {len(scored)} 只")
        return {
            "ok": True, "predict_date": trade_date_str, "target_date": target_date.isoformat(),
            "candidate_count": len(scored), "ingest": ingest_summary,
        }
    except Exception as e:  # noqa: BLE001
        session.rollback()
        logger.exception(f"[涨停预测] 打分落库失败: {e}")
        # 带上异常类型名：有些异常（如 queue.Empty）str() 为空，只回 str(e) 会得到
        # `error: ''`，上层完全看不出发生了什么。
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "ingest": ingest_summary}
    finally:
        session.close()


def get_pool_overview(trade_date_str: Optional[str] = None, include_zhaban: bool = True) -> Dict:
    """涨停池全览：家数/连板梯队/炸板率/赚钱效应，供 get_limit_up_pool 工具用。

    不传日期查"今天"且当日盘后批处理还没跑（DB 里还没有今天的定案数据）时，
    优先用 limit_up_scanner 的盘中内存缓存兜底（数据打 provisional 标记），
    而不是直接返回全 0——避免盘中问"今天涨停多少家"时答非所问。
    """
    is_today_query = trade_date_str is None
    if trade_date_str is None:
        trade_date_str = datetime.now().strftime("%Y%m%d")
    trade_date = datetime.strptime(trade_date_str, "%Y%m%d").date()

    session = get_session()
    try:
        if is_today_query:
            has_today_data = session.query(LimitUpPool).filter(
                LimitUpPool.trade_date == trade_date, LimitUpPool.pool_type == "zt",
            ).first() is not None
            if not has_today_data:
                from automation.limit_up_scanner import get_intraday_snapshot
                snapshot = get_intraday_snapshot()
                if snapshot:
                    return snapshot

        sentiment = metrics.get_market_sentiment(session, trade_date)
        # 上限 300 只是防病态用的安全帽，不是"只显示前N只"——A股涨停家数实际很少
        # 超过一两百，这里给 widget 的是完整清单，工具回给 LLM 的 slim 摘要另行截断。
        top_boards = session.query(LimitUpPool).filter(
            LimitUpPool.trade_date == trade_date, LimitUpPool.pool_type == "zt",
        ).order_by(
            LimitUpPool.consecutive_boards.desc().nullslast(),
            LimitUpPool.seal_amount.desc().nullslast(),
        ).limit(300).all()

        result = {
            **sentiment,
            "top_boards": [{
                "symbol": r.symbol, "name": r.name, "consecutive_boards": r.consecutive_boards,
                "seal_amount": r.seal_amount, "industry": r.industry,
                "zt_stat": f"{r.zt_stat_days}/{r.zt_stat_count}" if r.zt_stat_days else None,
            } for r in top_boards],
        }
        if include_zhaban:
            zb_rows = session.query(LimitUpPool).filter(
                LimitUpPool.trade_date == trade_date, LimitUpPool.pool_type == "zb",
            ).order_by(LimitUpPool.break_count.desc().nullslast()).limit(300).all()
            result["top_zhaban"] = [{
                "symbol": r.symbol, "name": r.name, "break_count": r.break_count, "industry": r.industry,
            } for r in zb_rows]
        return result
    finally:
        session.close()


def get_candidates(target_date_str: Optional[str] = None, top_n: int = 10) -> Dict:
    """候选池打分排名，供 predict_limit_up_candidates 工具用。不传日期取最新一批。"""
    session = get_session()
    try:
        query = session.query(LimitUpPrediction)
        if target_date_str:
            target_date = datetime.strptime(target_date_str, "%Y%m%d").date()
            query = query.filter(LimitUpPrediction.target_date == target_date)
        else:
            latest = session.query(LimitUpPrediction.target_date).order_by(
                LimitUpPrediction.target_date.desc()
            ).first()
            if not latest:
                return {"predict_date": None, "target_date": None, "candidates": []}
            target_date = latest[0]
            query = query.filter(LimitUpPrediction.target_date == target_date)

        rows = query.order_by(LimitUpPrediction.rank.asc()).limit(top_n).all()
        if not rows:
            return {"predict_date": None, "target_date": target_date.isoformat(), "candidates": []}

        return {
            "predict_date": rows[0].predict_date.isoformat(),
            "target_date": rows[0].target_date.isoformat(),
            "candidates": [{
                "symbol": r.symbol, "name": r.name, "score": r.score, "rank": r.rank, "source": r.source,
                "momentum_score": r.momentum_score, "capital_score": r.capital_score,
                "theme_score": r.theme_score, "sentiment_score": r.sentiment_score,
                "continuation_score": r.continuation_score,
                "reasons": json.loads(r.reasons) if r.reasons else [],
            } for r in rows],
        }
    finally:
        session.close()
