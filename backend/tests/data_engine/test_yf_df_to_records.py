"""yfinance DataFrame → 日线记录 —— 纯规则测试，零 DB。

**这个文件的存在源于一次真实故障**（2026-07-17）：美股增量跑了 18 分钟后死在
`NOT NULL constraint failed: daily_quotes.close`，后面 1 万只票一只都没跑到。
真凶是 yfinance 对停牌/无成交日返回 **NaN close**，而 **Python 的 sqlite3 把 NaN
静默转成 NULL**。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_engine.deep_history.bulk_upsert import _num, yf_df_to_records  # noqa: E402

NAN = float("nan")


def _df(rows):
    """rows: [(date, open, high, low, close, volume)]"""
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows],
         "Volume": [r[5] for r in rows]},
        index=idx,
    )


def test_normal_rows():
    df = _df([("2026-07-16", 373.05, 375.26, 352.29, 354.45, 41026700)])
    recs = yf_df_to_records("GOOGL", "us_stock", df)
    assert len(recs) == 1
    r = recs[0]
    assert r["symbol"] == "GOOGL" and r["market"] == "us_stock"
    assert r["date"] == "2026-07-16"
    assert r["close"] == pytest.approx(354.45)
    assert r["volume"] == 41026700


def test_nan_close_row_is_skipped_not_crashed():
    """⚠️ **这条就是那次故障的复现。**

    停牌/无成交那天 close 是 NaN。`float(nan)` **不抛异常**，NaN 会一路飘到 SQL 层，
    然后 sqlite3 把它转成 NULL → 撞 NOT NULL 约束 → 整批 executemany 炸。
    """
    df = _df([
        ("2026-07-15", 357.97, 373.64, 357.76, 370.92, 28284600),
        ("2026-07-16", NAN, NAN, NAN, NAN, NAN),      # 停牌
        ("2026-07-17", 355.00, 360.00, 354.00, 359.00, 1000),
    ])
    recs = yf_df_to_records("GOOGL", "us_stock", df)
    assert len(recs) == 2, "坏行跳过，好行照留"
    assert [r["date"] for r in recs] == ["2026-07-15", "2026-07-17"]
    assert all(r["close"] == r["close"] for r in recs), "留下的行不许含 NaN"


def test_partial_nan_in_ohlc_also_skipped():
    """只有 close 是 NaN 也要跳 —— 四列缺一不可，半根 bar 不是 bar。"""
    for bad in range(4):
        vals = [100.0, 110.0, 90.0, 105.0]
        vals[bad] = NAN
        df = _df([("2026-07-16", *vals, 1000)])
        assert yf_df_to_records("X", "us_stock", df) == [], f"第 {bad} 列 NaN 时应跳过"


def test_nan_volume_falls_back_to_zero_not_skipped():
    """成交量 NaN 只是没成交，OHLC 齐全就仍是有效的一根 bar —— 别把这行也扔了。"""
    df = _df([("2026-07-16", 100.0, 110.0, 90.0, 105.0, NAN)])
    recs = yf_df_to_records("X", "us_stock", df)
    assert len(recs) == 1
    assert recs[0]["volume"] == 0


def test_missing_column_does_not_crash():
    df = pd.DataFrame({"Open": [1.0]}, index=pd.to_datetime(["2026-07-16"]))
    assert yf_df_to_records("X", "us_stock", df) == []


def test_empty_df():
    assert yf_df_to_records("X", "us_stock", _df([])) == []


def test_num_helper():
    assert _num(1.5) == 1.5
    assert _num("2.5") == 2.5
    assert _num(NAN) is None, "NaN → None（NaN 是唯一不等于自己的值）"
    assert _num(None) is None
    assert _num("abc") is None
    assert _num(0) == 0.0, "0 是合法值，别被 falsy 坑了"


def test_both_overseas_paths_use_the_shared_impl():
    """深历史 + 每日增量必须共用同一份实现。

    **这条防的正是这次 bug 的成因**：两处各存一份逐字相同的副本，于是「只防了
    Volume 的 NaN、没防 OHLC」这个漏洞也存了两份，一处让整批数据被静默丢弃、
    一处让整个市场的 run 挂掉。
    """
    import inspect

    import data_engine.deep_history.overseas_job as oj
    import data_engine.overseas_daily_updater as odu

    for mod, cls in ((oj, oj.OverseasDeepHistoryJob), (odu, odu.OverseasDailyUpdater)):
        src = inspect.getsource(cls._df_to_records)
        assert "yf_df_to_records" in src, (
            f"{mod.__name__} 又自己实现了一份 _df_to_records —— 收口到 "
            f"bulk_upsert.yf_df_to_records，别让 bug 再分裂成两份"
        )
        assert "float(row[" not in src, f"{mod.__name__} 不该再直接 float(row[...])"
