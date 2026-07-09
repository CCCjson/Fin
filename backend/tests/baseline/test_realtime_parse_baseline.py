"""13.1 基线：东财实时行情原始响应 → 标准行情字典的归一化。

`_parse_items` 是纯函数，喂罐装 dict 即可测，不出网。
13.4-2 会把它搬进 `acquisition/markets/`，搬完这条必须还绿。
"""
import pytest

from data_engine.fetchers.realtime import FIELD_MAP, _parse_items, compute_statistics

pytestmark = pytest.mark.baseline

# 东财 push2 clist 接口的原始 item（f 字段），"-" 表示该字段无值
RAW_SH = {
    "f2": 1685.5, "f3": 1.23, "f4": 20.5, "f5": 31234, "f6": 5.2e9,
    "f7": 2.1, "f8": 0.25, "f9": 32.1, "f12": "600519", "f13": 1,
    "f14": "贵州茅台", "f15": 1690.0, "f16": 1660.0, "f17": 1665.0,
    "f18": 1665.0, "f20": 2.1e12, "f21": 2.1e12, "f23": 8.5, "f115": 31.8,
}
RAW_SZ = {
    "f2": 11.2, "f3": -0.89, "f4": -0.1, "f5": 1234567, "f6": 1.4e10,
    "f7": 1.8, "f8": 0.64, "f9": "-", "f12": "000001", "f13": 0,
    "f14": "平安银行", "f15": 11.4, "f16": 11.1, "f17": 11.3,
    "f18": 11.3, "f20": 2.2e11, "f21": 2.1e11, "f23": 0.55, "f115": "-",
}


def test_field_map_shape_unchanged():
    """字段映射是与东财的 wire 契约。改动它 = 全市场行情字段错位。"""
    assert FIELD_MAP["f2"] == "price"
    assert FIELD_MAP["f3"] == "change_pct"
    assert FIELD_MAP["f12"] == "code"
    assert FIELD_MAP["f13"] == "market_id"
    assert FIELD_MAP["f14"] == "name"
    assert len(FIELD_MAP) == 19


def test_parse_maps_every_field():
    (row,) = _parse_items([RAW_SH])
    assert row["price"] == 1685.5
    assert row["change_pct"] == 1.23
    assert row["name"] == "贵州茅台"
    assert row["code"] == "600519"
    assert row["high"] == 1690.0
    assert row["low"] == 1660.0
    assert row["open"] == 1665.0
    assert row["prev_close"] == 1665.0
    assert row["pe_ttm"] == 31.8


def test_dash_becomes_none_not_string():
    """东财用 "-" 表示无值。漏掉这个转换，下游 float("-") 会炸。"""
    (row,) = _parse_items([RAW_SZ])
    assert row["pe_ratio"] is None
    assert row["pe_ttm"] is None


def test_missing_field_becomes_none():
    (row,) = _parse_items([{"f12": "600519", "f13": 1}])
    assert row["price"] is None
    assert row["name"] is None


def test_symbol_built_from_market_id():
    sh, sz = _parse_items([RAW_SH, RAW_SZ])
    assert sh["symbol"] == "600519.SH"
    assert sz["symbol"] == "000001.SZ"


def test_market_id_zero_falls_back_to_sz():
    """特征测试，记录现状而非认可它：market_id != 1 一律拼 .SZ，产不出 .BJ。

    这是全仓「后缀实现不一致」的第三处（另两处已在 C1/C3 修掉）。
    北交所行情走这条路会拿到错的 symbol。A股库里现无北交所票，故未爆发。
    13.4-2 收编进 acquisition/markets/quote_router.py 时应改调 common.market。
    """
    (row,) = _parse_items([{"f12": "830799", "f13": 0}])
    assert row["symbol"] == "830799.SZ"  # 正确应为 830799.BJ


def test_parse_preserves_order_and_count():
    rows = _parse_items([RAW_SH, RAW_SZ, RAW_SH])
    assert len(rows) == 3
    assert [r["code"] for r in rows] == ["600519", "000001", "600519"]


def test_parse_empty_returns_empty_list():
    assert _parse_items([]) == []


class TestComputeStatistics:
    def test_counts_up_down_flat(self):
        quotes = [
            {"change_pct": 1.5}, {"change_pct": 3.0},
            {"change_pct": -0.8},
            {"change_pct": 0.0},
        ]
        stats = compute_statistics(quotes)
        assert stats == {"total": 4, "up": 2, "down": 1, "flat": 1,
                         "limit_up": 0, "limit_down": 0}

    def test_limit_up_and_down_thresholds(self):
        quotes = [
            {"change_pct": 9.95},    # 涨停
            {"change_pct": 10.0},
            {"change_pct": -9.98},   # 跌停
            {"change_pct": 5.0},     # 普通上涨
        ]
        stats = compute_statistics(quotes)
        # 阈值是 ±9.9（不是 ±10.0）—— ST 股/科创板涨跌幅不同，这个近似阈值是有意为之
        assert stats["limit_up"] == 2
        assert stats["limit_down"] == 1

    def test_none_change_pct_does_not_crash(self):
        stats = compute_statistics([{"change_pct": None}, {"change_pct": 1.0}])
        assert stats["up"] == 1
        assert stats["total"] == 2, "total 计所有行情，包括 change_pct 缺失的"

    def test_empty_quotes(self):
        stats = compute_statistics([])
        assert stats == {"total": 0, "up": 0, "down": 0, "flat": 0,
                         "limit_up": 0, "limit_down": 0}
