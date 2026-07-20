"""BTC 大势过滤 —— 加密波段最重要的一道闸。

90% 的山寨是 BTC 的影子，且加密有明显的牛熊周期。**只在 BTC 站上 200 日均线时做多
波段**、跌破就大幅收手/空仓，是新手最有效的护城河——躲开在熊市里逆势抄底的大坑。

- `compute_regime(df)`：纯函数，给一段 BTC 日线 DataFrame（需含 `close`），算出牛/熊。
- `btc_regime()`：便利函数，自动取 BTC 近 ~260 日线（DB 优先、缺则拉币安）后判定。

判定用 200 日 SMA（加密日线的经典大周期线）。样本不足 200 根时返回 `regime="unknown"`，
不硬凑（宁可说不知道，也不用半截均线误导波段决策）。
"""
from datetime import date, timedelta
from typing import Any

import pandas as pd

_MA_WINDOW = 200
_LOOKBACK_DAYS = 320  # 拉够 200 根 + 缓冲（含可能的缺口）


def compute_regime(df: pd.DataFrame, ma_window: int = _MA_WINDOW) -> dict[str, Any]:
    """给一段含 `close` 的日线 DataFrame，判 BTC 牛/熊。

    Returns:
        {regime: 'bull'|'bear'|'unknown', price, ma200, above, pct_from_ma}
        - regime='bull'：收盘价 ≥ 200MA（可做多波段）
        - regime='bear'：收盘价 < 200MA（收手/空仓）
        - regime='unknown'：样本不足 ma_window 根
        - pct_from_ma：价格偏离均线的百分比（正=在均线上方多少）
    """
    if df is None or df.empty or "close" not in df.columns or len(df) < ma_window:
        return {"regime": "unknown", "price": None, "ma200": None,
                "above": None, "pct_from_ma": None}

    closes = df["close"].astype(float)
    ma = float(closes.iloc[-ma_window:].mean())
    price = float(closes.iloc[-1])
    above = price >= ma
    pct = (price - ma) / ma * 100 if ma else None
    return {
        "regime": "bull" if above else "bear",
        "price": round(price, 2),
        "ma200": round(ma, 2),
        "above": above,
        "pct_from_ma": round(pct, 2) if pct is not None else None,
    }


def _load_btc_daily() -> pd.DataFrame:
    """取 BTC 近 ~320 日线：DB(daily_quotes) 优先，缺则实时拉币安。

    DB 优先是为了少打一次网（阶段2 上线后 DB 会常新）；DB 里不够 200 根就回退拉币安。
    """
    symbol = "BTCUSDT.BN"
    try:
        from sqlalchemy import text

        from data_engine.storage.database import get_session
        session = get_session()
        try:
            rows = session.execute(
                text("SELECT date, close FROM daily_quotes "
                     "WHERE symbol=:s AND market='crypto' ORDER BY date DESC LIMIT :n"),
                {"s": symbol, "n": _LOOKBACK_DAYS},
            ).fetchall()
        finally:
            session.close()
        if rows and len(rows) >= _MA_WINDOW:
            df = pd.DataFrame(rows, columns=["date", "close"]).iloc[::-1].reset_index(drop=True)
            return df
    except Exception:  # noqa: BLE001 — DB 不可用/表不存在，回退拉网
        pass

    # 回退：实时拉币安
    from acquisition.markets.base import MarketDataRequest
    from acquisition.markets.crypto import CryptoFetcher
    end = date.today()
    start = end - timedelta(days=_LOOKBACK_DAYS)
    resp = CryptoFetcher().fetch_daily(MarketDataRequest(
        symbol=symbol, start_date=start.isoformat(), end_date=end.isoformat(), freq="1d"))
    return resp.data


def btc_regime() -> dict[str, Any]:
    """BTC 当前大势（自动取数）。供波段策略/MoneyBill 做「能不能做多」的总闸。"""
    return compute_regime(_load_btc_daily())
