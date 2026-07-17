"""实时行情多市场取数：一个市场炸不许拖垮其它市场 —— 纯规则测试，零网络零 DB。

守着 P0-2 的一条硬要求：**失败要如实上报，且不许连坐**。

案发现场（2026-07-17 修复前，`data_engine/engine.py:290-292`）：整个多市场循环
包在**一个** try 里，`except Exception: return []`。后果是港股 yfinance 随便抛个
异常，就把 A 股**已经拿到**的行情一起丢掉，调用方收到一个空列表 —— 既不知道丢了
什么，也不知道为什么，更分不清「代理额度耗尽」和「源真的挂了」。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import data_engine.engine as eng_mod  # noqa: E402
from data_engine.engine import DataEngine  # noqa: E402
from net import ProxyExhaustedError  # noqa: E402


class _FakeFetcher:
    def __init__(self, rows=None, exc=None):
        self._rows = rows or []
        self._exc = exc

    def fetch_realtime(self, symbols):
        if self._exc is not None:
            raise self._exc
        return self._rows


def _patch_factory(monkeypatch, by_market):
    """把 FetcherFactory.create 换成按市场返回预设 fetcher 的假货。"""
    def _create(market, config):
        return by_market[market]
    monkeypatch.setattr(eng_mod.FetcherFactory, "create", staticmethod(_create))


@pytest.fixture
def engine(monkeypatch):
    # DataEngine.__init__ 不该出网/连库；只用它的方法，构造走真实路径即可
    return DataEngine()


def test_one_market_failure_does_not_discard_other_markets(monkeypatch, engine):
    """🔒 港股炸掉时，A 股已拿到的行情必须还在。

    这条红了 = 有人把按市场分别 try 又合回成一个 try。
    """
    _patch_factory(monkeypatch, {
        "a_share": _FakeFetcher(rows=[{"symbol": "600519.SH", "price": 1253.0}]),
        "hk_stock": _FakeFetcher(exc=RuntimeError("yfinance 炸了")),
    })
    quotes, failures = engine.get_realtime_quotes_with_status(["600519.SH", "00700.HK"])

    assert [q["symbol"] for q in quotes] == ["600519.SH"]   # A 股没被连坐
    assert failures == {"hk_stock": "fetch_failed"}         # 港股的失败如实上报


def test_proxy_exhausted_is_distinct_from_fetch_failed(monkeypatch, engine):
    """🔒 代理额度耗尽 ≠ 抓取失败。

    前者是「一个请求都没发出去」（铁律：绝不降级直连），重试无意义；后者是「发了但
    挂了」。此前两者都被 `except Exception` 抹平成同一个空列表。
    """
    _patch_factory(monkeypatch, {
        "a_share": _FakeFetcher(exc=ProxyExhaustedError("no ip")),
    })
    quotes, failures = engine.get_realtime_quotes_with_status(["600519.SH"])

    assert quotes == []
    assert failures == {"a_share": "proxy_exhausted"}


def test_all_markets_ok_reports_no_failures(monkeypatch, engine):
    """全部成功时 failures 必须是空的 —— 别让调用方误以为有问题。"""
    _patch_factory(monkeypatch, {
        "a_share": _FakeFetcher(rows=[{"symbol": "600519.SH", "price": 1.0}]),
        "us_stock": _FakeFetcher(rows=[{"symbol": "AAPL", "price": 2.0}]),
    })
    quotes, failures = engine.get_realtime_quotes_with_status(["600519.SH", "AAPL"])

    assert len(quotes) == 2
    assert failures == {}


def test_legacy_signature_still_returns_bare_list(monkeypatch, engine):
    """老调用方（trading_tools / data_tools）的形状不许漂移。"""
    _patch_factory(monkeypatch, {
        "a_share": _FakeFetcher(rows=[{"symbol": "600519.SH", "price": 1.0}]),
    })
    out = engine.get_realtime_quotes(["600519.SH"])
    assert isinstance(out, list)
    assert out[0]["symbol"] == "600519.SH"


def test_empty_symbols_short_circuits(monkeypatch, engine):
    """空入参不该构造任何 fetcher。"""
    def _boom(market, config):
        raise AssertionError("空入参不该碰 factory")
    monkeypatch.setattr(eng_mod.FetcherFactory, "create", staticmethod(_boom))
    assert engine.get_realtime_quotes_with_status([]) == ([], {})
