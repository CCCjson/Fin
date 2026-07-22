"""
盘后拉取涨停/炸板/跌停/昨日涨停 4 个池子 → 落 LimitUpPool 表。

强势股池（stock_zt_pool_strong_em）不在这里落库——它是候选打分的输入源，
由 candidate_pool.py 在打分时现拉现用，最终候选+打分结果落在 LimitUpPrediction
表里才是需要长期保留的记录（见方案「核心设计」一节）。
"""
from datetime import datetime, date as date_cls
from typing import Dict
from loguru import logger

from common.market import A_SHARE
from common.market_time import market_today

from data_engine.storage.database import get_session
from data_engine.storage.models import LimitUpPool
from acquisition.markets.limit_up import (
    fetch_limit_up_pool,
    fetch_limit_up_pool_previous,
    fetch_zhaban_pool,
    fetch_dieting_pool,
)

_POOL_FETCHERS = {
    "zt": fetch_limit_up_pool,
    "previous": fetch_limit_up_pool_previous,
    "zb": fetch_zhaban_pool,
    "dt": fetch_dieting_pool,
}

_ROW_FIELDS = [
    "change_pct", "price", "limit_price", "amount", "turnover", "amplitude", "speed",
    "circulating_mv", "total_mv", "seal_amount", "first_seal_time", "last_seal_time",
    "break_count", "consecutive_boards", "zt_stat_days", "zt_stat_count", "industry",
]


def _to_date(trade_date: str) -> date_cls:
    return datetime.strptime(trade_date, "%Y%m%d").date()


def ingest_trade_date(trade_date: str) -> Dict:
    """拉取某交易日 4 个池子并落库。先删同日同池数据再插，保证可重跑幂等。"""
    trade_date_obj = _to_date(trade_date)
    session = get_session()
    summary: Dict = {"trade_date": trade_date, "pools": {}}
    try:
        for pool_type, fetcher in _POOL_FETCHERS.items():
            try:
                rows = fetcher(trade_date)
            except ValueError as e:
                # 炸板池/跌停池仅支持最近30个交易日，超期会抛 ValueError，不中断其他池子
                logger.warning(f"[涨停池] {pool_type} 拉取失败: {e}")
                summary["pools"][pool_type] = {"error": str(e)}
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[涨停池] {pool_type} 拉取异常: {e}")
                summary["pools"][pool_type] = {"error": str(e)}
                continue

            session.query(LimitUpPool).filter(
                LimitUpPool.trade_date == trade_date_obj,
                LimitUpPool.pool_type == pool_type,
            ).delete()

            for row in rows:
                session.add(LimitUpPool(
                    trade_date=trade_date_obj,
                    symbol=row.get("symbol"),
                    name=row.get("name"),
                    pool_type=pool_type,
                    **{f: row.get(f) for f in _ROW_FIELDS},
                ))

            summary["pools"][pool_type] = {"count": len(rows)}
            logger.info(f"[涨停池] {pool_type} {trade_date} 落库 {len(rows)} 条")

        session.commit()
        summary["ok"] = True
    except Exception as e:  # noqa: BLE001
        session.rollback()
        logger.exception(f"[涨停池] 落库失败: {e}")
        summary["ok"] = False
        summary["error"] = str(e)
    finally:
        session.close()
    return summary


def run_daily_ingest(trade_date: str = None) -> Dict:
    """调度器/手动刷新入口，不传日期默认拉今天。"""
    if trade_date is None:
        trade_date = market_today(A_SHARE).strftime("%Y%m%d")
    return ingest_trade_date(trade_date)
