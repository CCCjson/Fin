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
    """涨跌停按板块阈值判定（主板 10% / 创业板·科创板 20%）。

    2026-07-10 之前这里是全市场一刀切 ±9.9%，注释自称「近似阈值是有意为之」——
    代价是创业板/科创板 10%~20% 的普通上涨全被记成涨停。
    `common.limit_rules` 是唯一真源（原先只有 limit_up_engine 在用）。

    **ST 的 5% 阈值刻意不启用**：本库里名字带 ST 的主板股 40.5% 涨跌幅超过 ±5%，
    name 与价格数据自相矛盾，靠名字判 ST 不可靠（详见 compute_statistics 的说明）。
    """

    def test_counts_up_down_flat(self):
        quotes = [
            {"symbol": "600519.SH", "change_pct": 1.5},
            {"symbol": "000001.SZ", "change_pct": 3.0},
            {"symbol": "600000.SH", "change_pct": -0.8},
            {"symbol": "601398.SH", "change_pct": 0.0},
        ]
        stats = compute_statistics(quotes)
        assert stats == {"total": 4, "up": 2, "down": 1, "flat": 1,
                         "limit_up": 0, "limit_down": 0}

    def test_main_board_limit_is_ten_percent(self):
        quotes = [
            {"symbol": "600519.SH", "change_pct": 9.95},    # 涨停
            {"symbol": "000001.SZ", "change_pct": 10.0},    # 涨停
            {"symbol": "600000.SH", "change_pct": -9.98},   # 跌停
            {"symbol": "601398.SH", "change_pct": 5.0},     # 普通上涨
        ]
        stats = compute_statistics(quotes)
        assert stats["limit_up"] == 2
        assert stats["limit_down"] == 1

    def test_gem_and_star_limit_is_twenty_percent(self):
        """创业板/科创板 15% 只是普通大涨，不是涨停——旧的 9.9 阈值会全部误判。"""
        quotes = [
            {"symbol": "300750.SZ", "change_pct": 15.0},    # 创业板，普通上涨
            {"symbol": "688371.SH", "change_pct": 15.0},    # 科创板，普通上涨
            {"symbol": "301158.SZ", "change_pct": 19.95},   # 创业板涨停
            {"symbol": "688001.SH", "change_pct": -19.98},  # 科创板跌停
        ]
        stats = compute_statistics(quotes)
        assert stats["limit_up"] == 1
        assert stats["limit_down"] == 1

    def test_st_threshold_is_deliberately_not_applied(self):
        """名字带 ST 的主板股按 10% 判，不按 5%——name 不可信，见类 docstring。

        `common.limit_rules.is_limit_up` 本身支持 ST（传 name 即可），
        只是全市场统计这条路径刻意不传。
        """
        quotes = [
            {"symbol": "600251.SH", "name": "*ST 冠农", "change_pct": 4.95},
            {"symbol": "600310.SH", "name": "ST 桂东电", "change_pct": -4.98},
        ]
        stats = compute_statistics(quotes)
        assert stats["limit_up"] == 0
        assert stats["limit_down"] == 0

        from common.limit_rules import is_limit_up
        assert is_limit_up("600251.SH", 4.95, "*ST 冠农") is True, "规则本身认 ST"
        assert is_limit_up("600251.SH", 4.95) is False, "不传 name 就按主板 10% 判"

    def test_late_listed_code_ranges_are_covered(self):
        """302xxx 中航成飞 / 689xxx 九号公司——3 位前缀的板块表曾把它们判成 unknown。"""
        quotes = [
            {"symbol": "302132.SZ", "change_pct": 19.95},   # 创业板涨停
            {"symbol": "689009.SH", "change_pct": 19.95},   # 科创板涨停
        ]
        assert compute_statistics(quotes)["limit_up"] == 2

    def test_out_of_scope_boards_never_count(self):
        """北交所（30% 且规则不同）与 B 股不在覆盖范围，一律不计涨跌停。"""
        quotes = [
            {"symbol": "430047.BJ", "change_pct": 29.9},
            {"symbol": "900901.SH", "change_pct": 9.95},
        ]
        stats = compute_statistics(quotes)
        assert stats["limit_up"] == 0
        assert stats["up"] == 2, "涨跌家数照常统计，只是不算涨停"

    def test_row_without_symbol_is_not_counted_as_limit(self):
        """没有 symbol 就判不出板块——不猜，不计。"""
        stats = compute_statistics([{"change_pct": 9.95}])
        assert stats["limit_up"] == 0
        assert stats["up"] == 1

    def test_none_change_pct_does_not_crash(self):
        stats = compute_statistics([{"change_pct": None}, {"change_pct": 1.0}])
        assert stats["up"] == 1
        assert stats["total"] == 2, "total 计所有行情，包括 change_pct 缺失的"

    def test_empty_quotes(self):
        stats = compute_statistics([])
        assert stats == {"total": 0, "up": 0, "down": 0, "flat": 0,
                         "limit_up": 0, "limit_down": 0}
