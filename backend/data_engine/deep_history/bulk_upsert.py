"""
日线批量写入的共用逻辑 —— 从 `data_engine/daily_updater.py` 抽出，
供日常增量更新器和深历史回补任务共用，避免两处维护同一段 INSERT OR REPLACE SQL。
"""
from datetime import datetime
from typing import Dict, List

from loguru import logger
from sqlalchemy import text


def bulk_upsert_quotes(session, records: List[Dict]) -> int:
    """用 INSERT OR REPLACE 批量写入日线数据。

    利用 daily_quotes 表上的唯一索引 idx_symbol_date(symbol, date) 做去重，
    重复区间重跑天然幂等。

    Args:
        session: SQLAlchemy session
        records: [{"symbol", "market", "date", "open", "high", "low",
                   "close", "volume", "amount", "turnover"}, ...]

    Returns:
        写入的记录数
    """
    if not records:
        return 0

    sql = text("""
        INSERT OR REPLACE INTO daily_quotes
            (symbol, market, date, open, high, low, close, volume, amount, turnover)
        VALUES
            (:symbol, :market, :date, :open, :high, :low, :close, :volume, :amount, :turnover)
    """)

    total = 0
    batch_size = 500
    conn = session.connection()
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        conn.execute(sql, batch)
        total += len(batch)
    session.commit()
    return total


def klines_to_records(symbol: str, market: str, klines: List[Dict]) -> List[Dict]:
    """东财 K 线 dict 列表转为 bulk_upsert_quotes 所需的记录格式"""
    records = []
    for row in klines:
        try:
            date_val = datetime.strptime(row["date"], "%Y-%m-%d").date()
            records.append({
                "symbol": symbol,
                "market": market,
                "date": date_val.isoformat(),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "amount": row.get("amount"),
                "turnover": row.get("turnover"),
            })
        except Exception as e:
            logger.warning(f"解析 {symbol} {row.get('date')} 失败: {e}")
    return records
