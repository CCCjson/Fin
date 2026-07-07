"""
涨停板行情数据获取器 - 基于 akshare 涨停池系列接口（东财 push2ex.eastmoney.com）

5 个接口字段已实测核对（非猜测），见 ~/.claude/plans/snazzy-dancing-cat.md：
- stock_zt_pool_em：今日涨停股池
- stock_zt_pool_previous_em：昨日涨停股池（其"涨跌幅"字段代表次日/今日表现，即赚钱效应数据源）
- stock_zt_pool_zbgc_em：炸板股池（仅最近 30 个交易日可查）
- stock_zt_pool_strong_em：强势股池（未封板，含"入选理由"）
- stock_zt_pool_dtgc_em：跌停股池（仅最近 30 个交易日可查）
"""
from typing import List, Dict
import pandas as pd
from loguru import logger


def _parse_zt_stat(raw) -> tuple:
    """涨停统计字段 "days/ct" 拆成 (窗口天数, 窗口内涨停次数)。已实测确认：
    例如 "4/3" 表示"最近4个交易日内3次涨停"。"""
    if not raw or not isinstance(raw, str) or "/" not in raw:
        return None, None
    try:
        days, ct = raw.split("/", 1)
        return int(days), int(ct)
    except (ValueError, TypeError):
        return None, None


def _row_symbol(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "900")):
        market_suffix = "SH"
    elif code.startswith(("8", "4", "92", "87", "83")):
        market_suffix = "BJ"  # 北交所（本功能不覆盖，见 limit_rules.get_board_type）
    else:
        market_suffix = "SZ"
    return f"{code}.{market_suffix}"


def fetch_limit_up_pool(trade_date: str) -> List[Dict]:
    """今日涨停股池。trade_date 格式 YYYYMMDD。"""
    import akshare as ak
    from net import domestic_akshare

    df = domestic_akshare(ak.stock_zt_pool_em, date=trade_date)
    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        days, ct = _parse_zt_stat(row.get("涨停统计"))
        result.append({
            "symbol": _row_symbol(row["代码"]),
            "name": row.get("名称"),
            "change_pct": row.get("涨跌幅"),
            "price": row.get("最新价"),
            "amount": row.get("成交额"),
            "circulating_mv": row.get("流通市值"),
            "total_mv": row.get("总市值"),
            "turnover": row.get("换手率"),
            "seal_amount": row.get("封板资金"),
            "first_seal_time": row.get("首次封板时间"),
            "last_seal_time": row.get("最后封板时间"),
            "break_count": row.get("炸板次数"),
            "consecutive_boards": row.get("连板数"),
            "zt_stat_days": days,
            "zt_stat_count": ct,
            "industry": row.get("所属行业"),
        })
    return result


def fetch_limit_up_pool_previous(trade_date: str) -> List[Dict]:
    """昨日涨停股池（含今日/次日表现）。"""
    import akshare as ak
    from net import domestic_akshare

    df = domestic_akshare(ak.stock_zt_pool_previous_em, date=trade_date)
    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        days, ct = _parse_zt_stat(row.get("涨停统计"))
        result.append({
            "symbol": _row_symbol(row["代码"]),
            "name": row.get("名称"),
            "change_pct": row.get("涨跌幅"),  # 次日/今日表现，赚钱效应数据源
            "price": row.get("最新价"),
            "limit_price": row.get("涨停价"),
            "amount": row.get("成交额"),
            "circulating_mv": row.get("流通市值"),
            "total_mv": row.get("总市值"),
            "turnover": row.get("换手率"),
            "speed": row.get("涨速"),
            "amplitude": row.get("振幅"),
            "first_seal_time": row.get("昨日封板时间"),
            "consecutive_boards": row.get("昨日连板数"),
            "zt_stat_days": days,
            "zt_stat_count": ct,
            "industry": row.get("所属行业"),
        })
    return result


def fetch_zhaban_pool(trade_date: str) -> List[Dict]:
    """炸板股池（仅最近 30 个交易日可查，超期 akshare 会抛 ValueError）。"""
    import akshare as ak
    from net import domestic_akshare

    df = domestic_akshare(ak.stock_zt_pool_zbgc_em, date=trade_date)
    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        days, ct = _parse_zt_stat(row.get("涨停统计"))
        result.append({
            "symbol": _row_symbol(row["代码"]),
            "name": row.get("名称"),
            "change_pct": row.get("涨跌幅"),
            "price": row.get("最新价"),
            "limit_price": row.get("涨停价"),
            "amount": row.get("成交额"),
            "circulating_mv": row.get("流通市值"),
            "total_mv": row.get("总市值"),
            "turnover": row.get("换手率"),
            "speed": row.get("涨速"),
            "amplitude": row.get("振幅"),
            "first_seal_time": row.get("首次封板时间"),
            "break_count": row.get("炸板次数"),
            "zt_stat_days": days,
            "zt_stat_count": ct,
            "industry": row.get("所属行业"),
        })
    return result


def fetch_strong_pool(trade_date: str) -> List[Dict]:
    """强势股池（未封板但强势，含"入选理由"：60日新高/近期多次涨停/两者皆是）。"""
    import akshare as ak
    from net import domestic_akshare

    df = domestic_akshare(ak.stock_zt_pool_strong_em, date=trade_date)
    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        days, ct = _parse_zt_stat(row.get("涨停统计"))
        result.append({
            "symbol": _row_symbol(row["代码"]),
            "name": row.get("名称"),
            "change_pct": row.get("涨跌幅"),
            "price": row.get("最新价"),
            "limit_price": row.get("涨停价"),
            "amount": row.get("成交额"),
            "circulating_mv": row.get("流通市值"),
            "total_mv": row.get("总市值"),
            "turnover": row.get("换手率"),
            "speed": row.get("涨速"),
            "is_new_high": row.get("是否新高"),      # "是"/"否"
            "volume_ratio": row.get("量比"),
            "select_reason": row.get("入选理由"),     # "60日新高"/"近期多次涨停"/"60日新高且近期多次涨停"
            "zt_stat_days": days,
            "zt_stat_count": ct,
            "industry": row.get("所属行业"),
        })
    return result


def fetch_dieting_pool(trade_date: str) -> List[Dict]:
    """跌停股池（仅最近 30 个交易日可查），用于大盘情绪对照（冰点指标）。"""
    import akshare as ak
    from net import domestic_akshare

    df = domestic_akshare(ak.stock_zt_pool_dtgc_em, date=trade_date)
    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        result.append({
            "symbol": _row_symbol(row["代码"]),
            "name": row.get("名称"),
            "change_pct": row.get("涨跌幅"),
            "price": row.get("最新价"),
            "amount": row.get("成交额"),
            "circulating_mv": row.get("流通市值"),
            "total_mv": row.get("总市值"),
            "turnover": row.get("换手率"),
            "seal_amount": row.get("封单资金"),
            "last_seal_time": row.get("最后封板时间"),
            "consecutive_boards": row.get("连续跌停"),
            "break_count": row.get("开板次数"),
            "industry": row.get("所属行业"),
        })
    return result
