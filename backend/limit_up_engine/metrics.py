"""
情绪指标计算 —— 连板梯队分布、炸板率、赚钱效应（昨日涨停股今日表现）。

全部基于 ingest.py 已落库的 LimitUpPool 数据做本地聚合，不再额外发请求。
"""
from typing import Dict
from datetime import date as date_cls

from data_engine.storage.models import LimitUpPool
# ST 的 5% 阈值全仓不启用（见 tests/limit_up_engine/test_st_policy.py 的证据链）
from common.limit_rules import is_limit_up


def get_ladder_distribution(session, trade_date: date_cls) -> Dict[str, int]:
    """连板梯队分布：{"1板": n, "2板": n, ..., "7板+": n}。akshare 已给出连板数，只做 groupby。"""
    rows = session.query(LimitUpPool.consecutive_boards).filter(
        LimitUpPool.trade_date == trade_date,
        LimitUpPool.pool_type == "zt",
    ).all()
    dist: Dict[str, int] = {}
    for (boards,) in rows:
        if boards is None:
            continue
        key = "7板+" if boards >= 7 else f"{boards}板"
        dist[key] = dist.get(key, 0) + 1
    return dist


def get_break_rate(session, trade_date: date_cls) -> Dict:
    """炸板率 = 炸板家数 / (炸板家数 + 涨停家数)。"""
    zt_count = session.query(LimitUpPool).filter(
        LimitUpPool.trade_date == trade_date, LimitUpPool.pool_type == "zt"
    ).count()
    zb_count = session.query(LimitUpPool).filter(
        LimitUpPool.trade_date == trade_date, LimitUpPool.pool_type == "zb"
    ).count()
    total = zt_count + zb_count
    rate = (zb_count / total) if total > 0 else None
    return {"limit_up_count": zt_count, "break_count": zb_count, "break_rate": rate}


def get_profit_effect(session, trade_date: date_cls) -> Dict:
    """赚钱效应：昨日涨停股今日（本条记录里的 change_pct）表现聚合。

    数据源是 previous 池：其 change_pct 字段代表"昨日涨停、今天涨跌幅"。
    """
    rows = session.query(
        LimitUpPool.symbol, LimitUpPool.name, LimitUpPool.change_pct
    ).filter(
        LimitUpPool.trade_date == trade_date,
        LimitUpPool.pool_type == "previous",
    ).all()
    if not rows:
        return {"sample_count": 0, "avg_change_pct": None, "up_ratio": None, "promotion_count": None}

    changes = [r.change_pct for r in rows if r.change_pct is not None]
    up_count = sum(1 for c in changes if c > 0)
    promotion_count = sum(
        1 for symbol, name, chg in rows
        if chg is not None and is_limit_up(symbol, chg)
    )
    return {
        "sample_count": len(rows),
        "avg_change_pct": sum(changes) / len(changes) if changes else None,
        "up_ratio": (up_count / len(changes)) if changes else None,
        "promotion_count": promotion_count,  # 昨日涨停、今日再次涨停（晋级）家数
    }


def get_industry_heat(session, trade_date: date_cls) -> Dict[str, int]:
    """题材热度输入：当日 zt 池按行业分组的涨停家数，供 scoring.py 的题材维度使用。"""
    rows = session.query(LimitUpPool.industry).filter(
        LimitUpPool.trade_date == trade_date, LimitUpPool.pool_type == "zt"
    ).all()
    heat: Dict[str, int] = {}
    for (industry,) in rows:
        if not industry:
            continue
        heat[industry] = heat.get(industry, 0) + 1
    return heat


def get_market_sentiment(session, trade_date: date_cls) -> Dict:
    """汇总大盘情绪快照：涨停家数/炸板率/赚钱效应/连板梯队，供 get_limit_up_pool 工具
    和 scoring.py 的大盘情绪维度共用。"""
    break_info = get_break_rate(session, trade_date)
    profit_effect = get_profit_effect(session, trade_date)
    ladder = get_ladder_distribution(session, trade_date)
    return {
        "trade_date": trade_date.isoformat(),
        "limit_up_count": break_info["limit_up_count"],
        "break_count": break_info["break_count"],
        "break_rate": break_info["break_rate"],
        "ladder_distribution": ladder,
        "profit_effect": profit_effect,
    }
