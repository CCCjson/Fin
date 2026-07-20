"""crypto 情报层落库 —— CryptoMetric / CryptoAsset 的幂等 upsert。

打分器（scorer.py）保持纯（不出网只算），采集器出网，**落库集中在这里**，一处维护
SQL 幂等口径。全部 INSERT OR REPLACE，撞唯一键即覆盖，重跑天然幂等。
"""
from datetime import date
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
