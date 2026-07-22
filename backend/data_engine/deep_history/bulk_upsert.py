"""
日线批量写入的共用逻辑 —— 从 `data_engine/daily_updater.py` 抽出，
供日常增量更新器和深历史回补任务共用，避免两处维护同一段 INSERT OR REPLACE SQL。
"""
from datetime import datetime
from typing import Dict, List

from loguru import logger
from sqlalchemy import text

from common.market_time import tz_of


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


def _num(v) -> float | None:
    """转 float；NaN / None / 转不动 → None。

    **`v != v` 是 NaN 的判定**（NaN 是唯一不等于自己的值）。`float(nan)` 不会抛异常，
    所以不做这个检查的话 NaN 会一路飘到 SQL 层。
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def yf_df_to_records(symbol: str, market: str, df) -> List[Dict]:
    """yfinance 的 DataFrame → `bulk_upsert_quotes` 认的记录格式。

    港美股两条路径（`deep_history/overseas_job.py` 深历史回补 + `overseas_daily_updater.py`
    每日增量）共用 —— **这两处原本各存了一份逐字相同的副本，于是 bug 也存了两份**，
    见下方。

    ## ⚠️ OHLC 的 NaN 必须在这里挡掉（2026-07-17 实测炸过）

    yfinance 对停牌/无成交的交易日会返回 **NaN 的 close**。而 **Python 的 sqlite3 把
    NaN 静默转成 NULL** → 撞 `daily_quotes.close` 的 NOT NULL 约束 → 整批 executemany
    失败。后果在两条路径上不同，但**都是错的**：
      - 每日增量：异常冒出去，**整个市场的 run 直接挂掉**（实测美股跑到一半死在 GOOGL 那批）
      - 深历史：`bulk_upsert` 外面有 try/except，**整批 50 只票的数据被静默丢弃**
        （日志只有一句「丢弃 N 条」）

    原来的副本**只防了 Volume**（`row.get("Volume") == row.get("Volume")` 就是 NaN 判定）
    **没防 OHLC** —— 作者知道 NaN 的存在，只是漏了这四列。

    正确做法是**逐行跳过坏行**，而不是让整批陪葬：一只票某天没成交，不该连累同批
    其它 49 只票。
    """
    records = []
    for idx, row in df.iterrows():
        try:
            o, h, low_, c = (_num(row["Open"]), _num(row["High"]),
                             _num(row["Low"]), _num(row["Close"]))
        except (KeyError, TypeError):
            continue
        if None in (o, h, low_, c):
            # 停牌/无成交那天 —— 跳过这一行，不是跳过这只票，更不是让整批陪葬
            continue
        day = _yf_index_to_market_date(idx, market, symbol)
        if day is None:
            continue
        records.append({
            "symbol": symbol, "market": market,
            "date": day,
            "open": o, "high": h, "low": low_, "close": c,
            "volume": _num(row.get("Volume")) or 0,
            "amount": None, "turnover": None,
        })
    return records


def _yf_index_to_market_date(idx, market: str, symbol: str) -> str | None:
    """yfinance 的 DatetimeIndex → **该市场交易日**的 `YYYY-MM-DD`。

    这是港美股 `daily_quotes.date` 的产生点，也是整条链上最脆的一环。

    yfinance 的日线 index 是**交易所本地 tz-aware** 的（美股 America/New_York、
    港股 Asia/Hong_Kong），所以原来的 `idx.date()` **碰巧是对的** —— 但那是运气：
    代码里没有任何断言，完全靠 yfinance 的版本行为（`requirements.txt` 只写了
    `yfinance>=0.2.35`）。而原来的 fallback `str(idx)[:10]` 更危险：index 一旦变成
    naive 或 UTC，港美股的日期就成了**未定义时区**，静默偏一天，还没人会发现。

    现在显式处理：
      - tz-aware → 换算到 `tz_of(market)` 再取日期（哪怕 yfinance 哪天改成给 UTC 也对）
      - naive    → 按交易所本地时间解读（yfinance 的历史行为），但**记 warning**
      - 都不是   → 返回 None 跳过这一行，绝不再靠字符串截断硬凑一个日期出来
    """
    if hasattr(idx, "tzinfo") and idx.tzinfo is not None:
        return idx.astimezone(tz_of(market)).date().isoformat()
    if hasattr(idx, "date"):
        logger.warning(f"{symbol}({market}) 的 yfinance 索引无时区，按交易所本地时间解读："
                       f"{idx!r} —— 若 yfinance 改成返回 UTC，港美股日期会整体偏一天")
        return idx.date().isoformat()
    logger.warning(f"{symbol}({market}) 的 yfinance 索引不是时间类型，跳过：{idx!r}")
    return None


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
