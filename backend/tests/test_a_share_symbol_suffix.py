"""AShareFetcher 的 symbol 后缀回归测试（离线，mock akshare）。

历史 bug：search_symbol / get_stock_list 内联 `"SH" if code.startswith("6") else "SZ"`，
北交所（43/83/87/88/920 段）会被拼成 .SZ 喂给 MoneyBill 的 search_stocks 工具，
后续按该 symbol 查 K 线必然落空。
"""
import sys
import types

import pandas as pd
import pytest

from acquisition.markets.a_share import AShareFetcher, _safe_symbol


class TestSafeSymbol:
    @pytest.mark.parametrize("code, expected", [
        ("600519", "600519.SH"),
        ("000001", "000001.SZ"),
        ("302132", "302132.SZ"),
        ("689009", "689009.SH"),
        ("830799", "830799.BJ"),   # 北交所：旧规则会给 .SZ
        ("920002", "920002.BJ"),
        ("430047", "430047.BJ"),
    ])
    def test_valid_codes(self, code, expected):
        assert _safe_symbol(code) == expected

    @pytest.mark.parametrize("code", ["999999", "ABC", "", "12345"])
    def test_dirty_code_returns_none_instead_of_raising(self, code):
        assert _safe_symbol(code) is None

    def test_strips_whitespace(self):
        assert _safe_symbol("  600519 ") == "600519.SH"


def _patch_akshare(monkeypatch, df):
    """把 domestic_akshare 换成返回固定 DataFrame 的桩，彻底不出网。"""
    import net
    monkeypatch.setattr(net, "domestic_akshare", lambda fn, *a, **kw: df, raising=True)


class TestGetStockList:
    def test_beijing_exchange_gets_bj_suffix(self, monkeypatch):
        df = pd.DataFrame([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "000001", "name": "平安银行"},
            {"code": "830799", "name": "艾能聚"},
            {"code": "920002", "name": "万达轴承"},
        ])
        _patch_akshare(monkeypatch, df)
        monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_info_a_code_name=object()))

        result = AShareFetcher().get_stock_list()
        symbols = [r["symbol"] for r in result]
        assert symbols == ["600519.SH", "000001.SZ", "830799.BJ", "920002.BJ"]

    def test_dirty_code_skipped_without_killing_list(self, monkeypatch):
        df = pd.DataFrame([
            {"code": "600519", "name": "贵州茅台"},
            {"code": "999999", "name": "不存在的票"},
            {"code": "300750", "name": "宁德时代"},
        ])
        _patch_akshare(monkeypatch, df)
        monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_info_a_code_name=object()))

        result = AShareFetcher().get_stock_list()
        assert [r["symbol"] for r in result] == ["600519.SH", "300750.SZ"]


class TestSearchSymbol:
    def test_beijing_exchange_gets_bj_suffix(self, monkeypatch):
        df = pd.DataFrame([
            {"代码": "830799", "名称": "艾能聚", "最新价": 10.5, "涨跌幅": 1.2},
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1500.0, "涨跌幅": -0.3},
        ])
        _patch_akshare(monkeypatch, df)
        monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_zh_a_spot_em=object()))

        result = AShareFetcher().search_symbol("")
        assert [r["symbol"] for r in result] == ["830799.BJ", "600519.SH"]
