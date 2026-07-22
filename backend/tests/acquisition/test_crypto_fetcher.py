"""币安现货 fetcher 的纯函数（不出网）——klines→df→落库记录 的形态锁死。

出网部分（fetch_daily/get_stock_list 真打币安）不在单测里跑，靠手动/集成验收。
这里只钉死解析与转换逻辑：币安 kline 数组的价量是字符串、openTime 是 UTC 毫秒、
坏行（NaN OHLC）逐行跳过不连累整批。
"""
import math

import pandas as pd

from acquisition.markets.crypto import (
    _date_to_ms,
    _klines_to_df,
    estimate_slippage,
    klines_df_to_records,
)


def _sample_kline(open_ms, o, h, low, c, v, quote_vol=0, taker_buy=0):
    # 币安 kline 12 字段：[openTime, o, h, l, c, volume, closeTime, quoteVolume,
    # trades, takerBuyBase, takerBuyQuote, ignore]，价量均为字符串
    return [open_ms, str(o), str(h), str(low), str(c), str(v), open_ms + 1,
            str(quote_vol), 0, str(taker_buy), "0", "0"]


def test_date_to_ms_utc():
    # 2026-07-18 00:00 UTC
    assert _date_to_ms("2026-07-18") == 1784332800000
    # end_of_day 取当天 23:59:59
    assert _date_to_ms("2026-07-18", end_of_day=True) == 1784332800000 + (23 * 3600 + 59 * 60 + 59) * 1000


def test_klines_to_df_parses_strings_and_utc_date():
    rows = [_sample_kline(1784332800000, 64000.1, 64900, 63800, 64834.22, 8031.18,
                          quote_vol=520_000_000, taker_buy=4015.59)]
    df = _klines_to_df(rows)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume",
                                "amount", "taker_buy_ratio"]
    r = df.iloc[0]
    assert str(r["date"]) == "2026-07-18"
    assert r["open"] == 64000.1 and r["close"] == 64834.22
    assert r["volume"] == 8031.18
    # 币安免费带的成交额与主动买入量：此前被丢弃，现保留喂流动性/资金流维
    assert r["amount"] == 520_000_000
    assert r["taker_buy_ratio"] == 0.5      # 主动买入正好一半 → 买卖势均


def test_klines_to_df_zero_volume_leaves_taker_ratio_none():
    """零成交时不造 0.5 的假中性（宁可标缺失也不喂假数据给打分器）。"""
    df = _klines_to_df([_sample_kline(1784332800000, 1, 1, 1, 1, 0)])
    assert df.iloc[0]["taker_buy_ratio"] is None


def test_klines_to_df_empty():
    assert _klines_to_df([]).empty


def test_records_carry_bn_suffix_and_crypto_market():
    df = _klines_to_df([_sample_kline(1784332800000, 1, 2, 0.5, 1.5, 100, quote_vol=150)])
    recs = klines_df_to_records("BTCUSDT.BN", df)
    assert len(recs) == 1
    r = recs[0]
    assert r["symbol"] == "BTCUSDT.BN"   # 落库保留项目内形态（带 .BN）
    assert r["market"] == "crypto"
    assert r["date"] == "2026-07-18"
    assert r["amount"] == 150            # 成交额落库（此前恒为 None）
    assert r["turnover"] is None         # 换手率需流通盘，币无此概念


# ──────────────── 盘口实测滑点（把 CostModel 的拍脑袋 0.05% 换成真盘口）────────────────


def _book():
    # 卖一 100 元 10 个（=1000 USDT），卖二 101 元 10 个，卖三 102 元 10 个
    return {"asks": [[100.0, 10.0], [101.0, 10.0], [102.0, 10.0]],
            "bids": [[99.0, 10.0], [98.0, 10.0], [97.0, 10.0]]}


def test_slippage_within_top_level_is_zero():
    """小单在卖一档内吃完 → 均价=最优价 → 零滑点。"""
    r = estimate_slippage(_book(), 500, "BUY")
    assert r["slippage_pct"] == 0.0
    assert r["avg_price"] == 100.0 and r["exhausted"] is False


def test_slippage_eats_through_levels():
    """吃穿两档：1000 USDT@100 + 505 USDT@101 → 均价高于最优价，滑点为正。"""
    r = estimate_slippage(_book(), 1505, "BUY")
    assert r["avg_price"] > 100.0
    assert 0 < r["slippage_pct"] < 0.01
    assert r["exhausted"] is False


def test_slippage_sell_side_uses_bids_and_stays_positive():
    """SELL 吃 bids、卖便宜了同样记为正滑点（成本方向统一，别出现负成本）。"""
    r = estimate_slippage(_book(), 1500, "SELL")
    assert r["best_price"] == 99.0
    assert r["avg_price"] < 99.0
    assert r["slippage_pct"] > 0


def test_slippage_flags_exhausted_book():
    """单簿吃光还没凑够金额 → exhausted=True（流动性不足，调用方须当高风险）。"""
    r = estimate_slippage(_book(), 10_000_000, "BUY")
    assert r["exhausted"] is True


def test_slippage_empty_book_returns_none():
    assert estimate_slippage({"asks": [], "bids": []}, 1000, "BUY") is None
    assert estimate_slippage(None, 1000, "BUY") is None


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
