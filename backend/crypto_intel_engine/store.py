"""crypto 情报层落库 —— CryptoMetric / CryptoAsset / TokenUnlock / CryptoBar 的幂等读写。

打分器（scorer.py）保持纯（不出网只算），采集器出网，**落库集中在这里**，一处维护
SQL 幂等口径。全部 INSERT OR REPLACE，撞唯一键即覆盖，重跑天然幂等。

## 读取器为什么也在这里

打分从「快照阈值」升级到「历史分位」之后，打分器需要读回 `crypto_metrics` 的时序
（资金费率现在这个值在过去 60 天里算高还是低）。读写同表、同一套 symbol/metric 口径，
放一处才不会漂。读取器返回**纯 list**，打分器仍是纯函数（吃 list 不碰 DB）。
"""
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

MARKET_SENTINEL = "MARKET"   # 市场级指标（恐慌贪婪/主导率/总市值）的 symbol 哨兵


def upsert_metric(session, symbol: str, day: date, metric: str,
                  value: float | None, source: str) -> None:
    """写一条时序指标（撞 (symbol,date,metric) 唯一键即覆盖）。value=None 跳过（不写空洞）。"""
    if value is None:
        return
    session.connection().execute(text("""
        INSERT OR REPLACE INTO crypto_metrics (symbol, market, date, metric, value, source)
        VALUES (:symbol, 'crypto', :date, :metric, :value, :source)
    """), {"symbol": symbol, "date": day.isoformat(), "metric": metric,
           "value": float(value), "source": source})


def upsert_market_metrics(session, day: date, metrics: dict[str, float | None],
                          source: str) -> int:
    """批量写市场级指标（symbol='MARKET'）。返回写入条数（跳过 None 的）。"""
    n = 0
    for metric, value in metrics.items():
        if value is not None:
            upsert_metric(session, MARKET_SENTINEL, day, metric, value, source)
            n += 1
    session.commit()
    return n


def read_metric_map(session, symbol: str, metric: str, days: int = 90) -> dict[str, float]:
    """读某指标近 `days` 天的历史值，返回 **{date_str: value}**（跳过空洞）。

    回测闸的逐日回放要按 bar 日期查值，光有 list 对不上时间轴 —— 故按日索引这一份是真源，
    `read_metric_series` 只是它按日期升序取值的视图（同一条 SQL，不写两份口径）。
    """
    since = (datetime.now().date() - timedelta(days=days)).isoformat()
    rows = session.execute(text("""
        SELECT date, value FROM crypto_metrics
        WHERE symbol = :symbol AND metric = :metric AND date >= :since AND value IS NOT NULL
        ORDER BY date ASC
    """), {"symbol": symbol, "metric": metric, "since": since}).fetchall()
    return {str(r[0]): float(r[1]) for r in rows}


def read_market_metric_map(session, metric: str, days: int = 90) -> dict[str, float]:
    """读市场级指标（symbol='MARKET'）的 {date_str: value}，如稳定币总供应/恐慌贪婪。"""
    return read_metric_map(session, MARKET_SENTINEL, metric, days)


def read_metric_series(session, symbol: str, metric: str, days: int = 90) -> list[float]:
    """读某指标近 `days` 天的历史值（按日期升序，跳过空洞）。供打分器算分位/变化率。

    打分器拿到的是**纯 list[float]**，保持它「不碰 DB」的纯函数属性。
    """
    return list(read_metric_map(session, symbol, metric, days).values())


def read_market_series(session, metric: str, days: int = 90) -> list[float]:
    """读市场级指标（symbol='MARKET'）的历史序列，如稳定币总供应/恐慌贪婪。"""
    return read_metric_series(session, MARKET_SENTINEL, metric, days)


def upsert_unlocks(session, symbol: str, events: list[dict], source: str = "defillama") -> int:
    """写解锁事件（撞 (symbol, unlock_date, category) 唯一键即覆盖）。返回写入条数。

    同日同类别可能有多条（DefiLlama 会拆多笔 cliff）→ **按唯一键合并求和**，
    否则 INSERT OR REPLACE 只会留下最后一条，金额被吞。
    """
    merged: dict[tuple, float] = {}
    for e in events:
        d, cat = e.get("unlock_date"), e.get("category") or "unknown"
        amt = e.get("amount")
        if d is None or not isinstance(amt, (int, float)):
            continue
        key = (d.isoformat() if hasattr(d, "isoformat") else str(d), cat)
        merged[key] = merged.get(key, 0.0) + float(amt)

    for (day, cat), amt in merged.items():
        session.connection().execute(text("""
            INSERT OR REPLACE INTO token_unlocks
                (symbol, unlock_date, amount, pct_of_supply, category, source, updated_at)
            VALUES (:symbol, :unlock_date, :amount, :pct, :category, :source, CURRENT_TIMESTAMP)
        """), {"symbol": symbol, "unlock_date": day, "amount": amt,
               "pct": None, "category": cat, "source": source})
    session.commit()
    return len(merged)


def upsert_bars(session, bars: list[dict]) -> int:
    """写日内 K 线（撞 (symbol, interval, open_time) 唯一键即覆盖）。返回写入条数。

    末根 K 线是**未收盘**的，会被下一轮覆盖成最终值——这正是要 REPLACE 不要 IGNORE 的原因。
    """
    n = 0
    for b in bars:
        ot = b.get("open_time")
        if ot is None:
            continue
        session.connection().execute(text("""
            INSERT OR REPLACE INTO crypto_bars
                (symbol, interval, open_time, open, high, low, close, volume,
                 quote_volume, trades, taker_buy_ratio)
            VALUES (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume,
                    :quote_volume, :trades, :taker_buy_ratio)
        """), {
            "symbol": b["symbol"], "interval": b["interval"],
            "open_time": ot.replace(tzinfo=None) if hasattr(ot, "replace") else ot,
            "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
            "volume": b["volume"], "quote_volume": b.get("quote_volume"),
            "trades": b.get("trades"), "taker_buy_ratio": b.get("taker_buy_ratio"),
        })
        n += 1
    session.commit()
    return n


def read_bars(session, symbol: str, interval: str = "4h", limit: int = 200) -> list[dict]:
    """读最近 `limit` 根日内 K 线（**按时间升序**返回，喂指标计算）。"""
    rows = session.execute(text("""
        SELECT open_time, open, high, low, close, volume, quote_volume, taker_buy_ratio
        FROM crypto_bars WHERE symbol = :symbol AND interval = :interval
        ORDER BY open_time DESC LIMIT :limit
    """), {"symbol": symbol, "interval": interval, "limit": limit}).fetchall()
    out = [{"open_time": r[0], "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5], "quote_volume": r[6],
            "taker_buy_ratio": r[7]} for r in rows]
    return list(reversed(out))


def upsert_asset(session, symbol: str, snapshot: dict[str, Any]) -> None:
    """写代币经济学静态快照（主键 symbol，覆盖式）。snapshot 键对齐 CryptoAsset 列。"""
    cols = ("base_asset", "name", "coingecko_id", "circulating_supply", "max_supply",
            "total_supply", "market_cap", "fdv", "inflation_flag", "github_repo")
    row = {"symbol": symbol}
    for c in cols:
        row[c] = snapshot.get(c)
    session.connection().execute(text("""
        INSERT OR REPLACE INTO crypto_assets
            (symbol, base_asset, name, coingecko_id, circulating_supply, max_supply,
             total_supply, market_cap, fdv, inflation_flag, github_repo, updated_at)
        VALUES
            (:symbol, :base_asset, :name, :coingecko_id, :circulating_supply, :max_supply,
             :total_supply, :market_cap, :fdv, :inflation_flag, :github_repo, CURRENT_TIMESTAMP)
    """), row)
    session.commit()
