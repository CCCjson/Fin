"""
候选股筛选 —— 决定"谁有资格进入打分候选名单"。

核心原则（见方案「核心设计」一节，Jason 明确要求）：已封板的今日涨停池(zt)
不进候选——那些票买不进，只能当参考/情绪数据。候选来源是三路"当前能买"的股票：

1. strong        强势股池（东财官方筛选，未封板但强势）
2. continuation  昨日涨停、今日仍强势未回落（LimitUpPool previous 池）
3. quasi         自建准涨停扫描（全市场快照里涨幅逼近但未到涨停阈值的票，纯本地计算）

三路去重优先级：strong > continuation > quasi（同一票被多路命中时，保留信息更丰富的来源）。
"""
from typing import Dict, List, Optional
from datetime import date as date_cls

from loguru import logger

from data_engine.storage.models import LimitUpPool, StockInfo
from limit_up_engine.limit_rules import get_limit_threshold, is_limit_up
from data_engine.fetchers.limit_up import fetch_strong_pool

QUASI_LOW_RATIO = 0.5     # 准涨停扫描：涨幅至少达到该股涨停阈值的 50%
QUASI_HIGH_RATIO = 0.99   # 上限，双重保险防止已封板的票混入（zt池本已排除这类票）
QUASI_MIN_TURNOVER = 2.0  # 换手率下限 %，过滤缩量假突破
CONTINUATION_MIN_CHANGE = 3.0  # 昨涨停、今日涨幅至少达到这个百分比才算"仍强势未回落"


def _is_supported_board(symbol: str, name: Optional[str] = None) -> bool:
    """MVP 明确排除北交所/未知板块（降低复杂度，见方案风险点第4条）。"""
    return get_limit_threshold(symbol, name) is not None


def _candidate_from_strong_pool(trade_date_str: str) -> List[Dict]:
    try:
        rows = fetch_strong_pool(trade_date_str)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[候选池] 强势股池拉取失败: {e}")
        return []
    candidates = []
    for row in rows:
        symbol, name = row["symbol"], row.get("name")
        if not _is_supported_board(symbol, name):
            continue
        # 东财"强势股池"不保证未封板——它按"近期强势"筛选，可能包含今日已经涨停的票
        # （比如"近期多次涨停"的票今天可能又封板了）。这类必须排除，否则违反"候选=能买进"的原则。
        if is_limit_up(symbol, row.get("change_pct"), name):
            continue
        candidates.append({
            "symbol": symbol, "name": name, "source": "strong",
            "change_pct": row.get("change_pct"), "turnover": row.get("turnover"),
            "amount": row.get("amount"), "circulating_mv": row.get("circulating_mv"),
            "volume_ratio": row.get("volume_ratio"), "select_reason": row.get("select_reason"),
            "is_new_high": row.get("is_new_high"), "industry": row.get("industry"),
            "consecutive_boards": None, "limit_threshold": None, "approach_ratio": None,
        })
    return candidates


def _candidate_from_continuation(session, trade_date: date_cls) -> List[Dict]:
    rows = session.query(LimitUpPool).filter(
        LimitUpPool.trade_date == trade_date,
        LimitUpPool.pool_type == "previous",
    ).all()
    candidates = []
    for row in rows:
        if not _is_supported_board(row.symbol, row.name):
            continue
        if row.change_pct is None or row.change_pct < CONTINUATION_MIN_CHANGE:
            continue
        if is_limit_up(row.symbol, row.change_pct, row.name):
            continue  # 今天又封板了，归 zt 池范畴，不重复放进候选
        candidates.append({
            "symbol": row.symbol, "name": row.name, "source": "continuation",
            "change_pct": row.change_pct, "turnover": row.turnover,
            "amount": row.amount, "circulating_mv": row.circulating_mv,
            "volume_ratio": None, "select_reason": None, "is_new_high": None,
            "industry": row.industry, "consecutive_boards": row.consecutive_boards,
            "limit_threshold": None, "approach_ratio": None,
        })
    return candidates


def _candidate_from_quasi_scan(session, quotes: List[Dict]) -> List[Dict]:
    """从全市场实时快照（收盘后调用即代表当日收盘数据）里筛"离涨停很近但未封板"的票。"""
    prelim = []
    for q in quotes:
        symbol = q.get("symbol")
        name = q.get("name")
        change_pct = q.get("change_pct")
        if not symbol or change_pct is None:
            continue
        threshold = get_limit_threshold(symbol, name)
        if threshold is None:  # 北交所/未知板块不覆盖
            continue
        if is_limit_up(symbol, change_pct, name):
            continue  # 已封板，不是"准涨停"，交给 zt 池当参考数据
        ratio = change_pct / threshold if threshold else 0
        if ratio < QUASI_LOW_RATIO or ratio > QUASI_HIGH_RATIO:
            continue
        turnover = q.get("turnover")
        if turnover is not None and turnover < QUASI_MIN_TURNOVER:
            continue
        prelim.append((q, symbol, name, change_pct, threshold, ratio, turnover))

    if not prelim:
        return []

    # 补行业标签：quasi 来源是实时快照，没有行业字段，从 StockInfo 批量查一次
    # （题材热度维度需要它，否则 quasi 候选全给中性分，见 scoring.theme_score）。
    symbols = [p[1] for p in prelim]
    industry_map = {
        row.symbol: row.industry
        for row in session.query(StockInfo.symbol, StockInfo.industry).filter(StockInfo.symbol.in_(symbols)).all()
    }

    candidates = []
    for q, symbol, name, change_pct, threshold, ratio, turnover in prelim:
        candidates.append({
            "symbol": symbol, "name": name, "source": "quasi",
            "change_pct": change_pct, "turnover": turnover,
            "amount": q.get("amount"), "circulating_mv": q.get("circ_mv"),
            "volume_ratio": None, "select_reason": None, "is_new_high": None,
            "industry": industry_map.get(symbol), "consecutive_boards": None,
            "limit_threshold": threshold, "approach_ratio": ratio,
        })
    return candidates


def build_candidate_pool(
    session, trade_date: date_cls, trade_date_str: str, quotes: Optional[List[Dict]] = None
) -> List[Dict]:
    """三路来源合并去重（优先级 strong > continuation > quasi）。

    quotes: 全市场实时快照，不传则内部现拉一次（`fetch_a_share_realtime_cached`）。
    这是盘后批处理里的单次调用，不是重复轮询，成本可接受（区别于阶段二盘中轮询）。
    """
    if quotes is None:
        from data_engine.fetchers.realtime import fetch_a_share_realtime_cached
        quotes = fetch_a_share_realtime_cached(ttl=20.0)

    merged: Dict[str, Dict] = {}
    for row in _candidate_from_strong_pool(trade_date_str):
        merged[row["symbol"]] = row
    for row in _candidate_from_continuation(session, trade_date):
        merged.setdefault(row["symbol"], row)
    for row in _candidate_from_quasi_scan(session, quotes):
        merged.setdefault(row["symbol"], row)

    logger.info(f"[候选池] {trade_date_str} 候选 {len(merged)} 只（strong/continuation/quasi 合并去重后）")
    return list(merged.values())
