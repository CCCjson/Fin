"""币安现货 fetcher 的纯函数（不出网）——klines→df→落库记录 的形态锁死。

出网部分（fetch_daily/get_stock_list 真打币安）不在单测里跑，靠手动/集成验收。
这里只钉死解析与转换逻辑：币安 kline 数组的价量是字符串、openTime 是 UTC 毫秒、
坏行（NaN OHLC）逐行跳过不连累整批。
"""
import math

import pandas as pd

from acquisition.markets.crypto import _date_to_ms, _klines_to_df, klines_df_to_records


def _sample_kline(open_ms, o, h, low, c, v):
    # 币安 kline：[openTime, open, high, low, close, volume, closeTime, ...] 价量为字符串
    return [open_ms, str(o), str(h), str(low), str(c), str(v), open_ms + 1, "0", 0, "0", "0", "0"]


def test_date_to_ms_utc():
    # 2026-07-18 00:00 UTC
    assert _date_to_ms("2026-07-18") == 1784332800000
    # end_of_day 取当天 23:59:59
    assert _date_to_ms("2026-07-18", end_of_day=True) == 1784332800000 + (23 * 3600 + 59 * 60 + 59) * 1000


def test_klines_to_df_parses_strings_and_utc_date():
    rows = [_sample_kline(1784332800000, 64000.1, 64900, 63800, 64834.22, 8031.18)]
    df = _klines_to_df(rows)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    r = df.iloc[0]
    assert str(r["date"]) == "2026-07-18"
    assert r["open"] == 64000.1 and r["close"] == 64834.22
    assert r["volume"] == 8031.18


def test_klines_to_df_empty():
    assert _klines_to_df([]).empty


def test_records_carry_bn_suffix_and_crypto_market():
    df = _klines_to_df([_sample_kline(1784332800000, 1, 2, 0.5, 1.5, 100)])
    recs = klines_df_to_records("BTCUSDT.BN", df)
    assert len(recs) == 1
    r = recs[0]
    assert r["symbol"] == "BTCUSDT.BN"   # 落库保留项目内形态（带 .BN）
    assert r["market"] == "crypto"
    assert r["date"] == "2026-07-18"
    assert r["amount"] is None and r["turnover"] is None


def test_records_skip_nan_ohlc_rows():
    """含 NaN OHLC 的坏行逐行跳过，不让整批陪葬（同 yf 路径教训）。"""
    d1, d2 = pd.Timestamp("2026-07-17").date(), pd.Timestamp("2026-07-18").date()
    df = pd.DataFrame([
        {"date": d1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"date": d2, "open": math.nan, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
    ])
    recs = klines_df_to_records("ETHUSDT.BN", df)
    assert len(recs) == 1
    assert recs[0]["date"] == "2026-07-17"
