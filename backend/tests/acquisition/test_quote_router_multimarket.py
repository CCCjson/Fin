"""跨市场实时行情出口（S6）。

S6 之前 `quote_router.fetch_quotes` 第一行就是
`targets = [s for s in symbols if _to_prefixed(s)]` —— **非沪深标的静默消失**。
而它是四个功能共用的取价出口：

| 消费方 | 症状 |
|---|---|
| `price_alert_monitor` | 预警不告警、不报错、不写日志 |
| `position_guardian` | **止损守护全瞎**（风控） |
| `portfolio_tools` | 盘中估值退回 EOD |
| `intraday_tools` | 盘中查价拿不到 |

本文件钉死三件事：**四个市场都拿得到**、**crypto 的涨跌幅不是 None**、
**闭市不发请求且不算失败**。
"""
from unittest.mock import patch

import pytest

from acquisition.markets import quote_router as qr
from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK


class _FakeFetcher:
    """按市场返回一行；记下被调用过几次。"""
    calls: list[tuple[str, tuple]] = []

    def __init__(self, market, rows):
        self.market, self.rows = market, rows

    def fetch_realtime(self, symbols):
        _FakeFetcher.calls.append((self.market, tuple(symbols)))
        return [r for r in self.rows if r["symbol"] in symbols]


@pytest.fixture(autouse=True)
def _reset():
    _FakeFetcher.calls = []
    yield
    _FakeFetcher.calls = []


def _patch_fetchers(rows_by_market):
    def _create(market, config=None):
        return _FakeFetcher(market, rows_by_market.get(market, []))
    return patch("acquisition.markets.factory.FetcherFactory.create", _create)


# ── 1. 四市场都拿得到 ────────────────────────────────────────────────────

def test_all_four_markets_come_back(monkeypatch):
    monkeypatch.setattr(qr, "_fetch_a_share",
                        lambda syms: [qr._canonical("600519.SH", price=1650.0)])
    rows = {
        HK_STOCK: [{"symbol": "00700.HK", "price": 380.0, "change_percent": 1.2}],
        US_STOCK: [{"symbol": "AAPL", "price": 190.0, "change_percent": -0.5}],
        CRYPTO: [{"symbol": "BTCUSDT.BN", "price": 65000.0, "change_percent": 2.3}],
    }
    with _patch_fetchers(rows), patch(
            "common.market_session.is_open", lambda m, now=None: True):
        out = qr.fetch_quotes_detailed(
            ["600519.SH", "00700.HK", "AAPL", "BTCUSDT.BN"])

    got = {q["symbol"] for q in out["quotes"]}
    assert got == {"600519.SH", "00700.HK", "AAPL", "BTCUSDT.BN"}
    assert out["missing"] == []
    assert out["by_market"] == {A_SHARE: 1, HK_STOCK: 1, US_STOCK: 1, CRYPTO: 1}


def test_a_share_chain_is_untouched(monkeypatch):
    """沪深那条腾讯→新浪→东财→百度的链一个字没动 —— 只是被包了一层路由。"""
    seen = {}

    def _spy(syms):
        seen["syms"] = syms
        return []

    monkeypatch.setattr(qr, "_fetch_a_share", _spy)
    with _patch_fetchers({}):
        qr.fetch_quotes_detailed(["600519.SH", "000001.SZ", "AAPL"])
    assert seen["syms"] == ["600519.SH", "000001.SZ"]     # 美股没混进来


# ── 2. 缺失不静默 ────────────────────────────────────────────────────────

def test_missing_symbols_are_reported_not_swallowed(monkeypatch):
    """🔴 「少一行」正是 P1-4 记录的那种「不告警、不报错、不写日志」。"""
    monkeypatch.setattr(qr, "_fetch_a_share", lambda syms: [])
    with _patch_fetchers({US_STOCK: []}), patch(
            "common.market_session.is_open", lambda m, now=None: True):
        out = qr.fetch_quotes_detailed(["600519.SH", "AAPL"])
    assert out["quotes"] == []
    assert out["missing"] == ["600519.SH", "AAPL"]


def test_one_market_failing_does_not_take_down_the_others(monkeypatch):
    monkeypatch.setattr(qr, "_fetch_a_share",
                        lambda syms: [qr._canonical("600519.SH", price=1650.0)])

    def _boom(market, config=None):
        if market == US_STOCK:
            raise RuntimeError("yfinance 挂了")
        return _FakeFetcher(market, [])

    with patch("acquisition.markets.factory.FetcherFactory.create", _boom), patch(
            "common.market_session.is_open", lambda m, now=None: True):
        out = qr.fetch_quotes_detailed(["600519.SH", "AAPL"])
    assert [q["symbol"] for q in out["quotes"]] == ["600519.SH"]
    assert out["missing"] == ["AAPL"]


# ── 3. 闭市不打接口 ──────────────────────────────────────────────────────

def test_closed_market_is_not_requested_at_all():
    """⛔ 闭市的价不会变，去取纯属白烧 yfinance 配额。**闭市直接不发请求**。"""
    with _patch_fetchers({US_STOCK: [{"symbol": "AAPL", "price": 190.0}]}), patch(
            "common.market_session.is_open", lambda m, now=None: False):
        out = qr.fetch_quotes_detailed(["AAPL"])
    assert _FakeFetcher.calls == [], "闭市还发了请求"
    assert out["skipped_closed"] == ["AAPL"]
    # ⭐ 「刻意没取」不是「取失败」，不能混进 missing
    assert out["missing"] == []


def test_crypto_is_never_skipped_for_being_closed():
    """crypto 7×24 —— 把 A 股的会话假设套到它头上，它会在大部分时间里瞎掉。"""
    rows = {CRYPTO: [{"symbol": "BTCUSDT.BN", "price": 65000.0,
                      "change_percent": 2.3}]}
    from datetime import datetime
    with _patch_fetchers(rows):
        # 周六凌晨三点：股票全休市
        with patch("common.market_session.market_now",
                   lambda m: datetime.fromisoformat("2026-08-01T03:00:00")):
            out = qr.fetch_quotes_detailed(["BTCUSDT.BN"])
    assert out["skipped_closed"] == []
    assert out["quotes"][0]["price"] == 65000.0


# ── 4. 形状归一 ──────────────────────────────────────────────────────────

def test_crypto_change_percent_is_not_none():
    """🔴 `pct_change` 类型的预警**只看 change_percent**。

    只给 price 的话，crypto 涨跌幅预警会从「拿不到行情」变成「拿到了但字段是 None」——
    **更隐蔽地恒不触发**。
    """
    rows = {CRYPTO: [{"symbol": "BTCUSDT.BN", "price": 65000.0,
                      "change_percent": 2.34, "volume": 1234.0}]}
    with _patch_fetchers(rows):
        out = qr.fetch_quotes_detailed(["BTCUSDT.BN"])
    q = out["quotes"][0]
    assert q["change_percent"] == 2.34
    assert q["price"] == 65000.0
    assert q["source"] == CRYPTO


def test_legacy_change_pct_key_is_also_accepted():
    """历史上还有 `change_pct` 的写法，两个都认，别让下游拿到 None。"""
    rows = {HK_STOCK: [{"symbol": "00700.HK", "price": 380.0, "change_pct": 1.5}]}
    with _patch_fetchers(rows), patch(
            "common.market_session.is_open", lambda m, now=None: True):
        out = qr.fetch_quotes_detailed(["00700.HK"])
    assert out["quotes"][0]["change_percent"] == 1.5


def test_missing_fields_stay_none_not_zero():
    """⛔ 缺字段留 None 不用 0 顶替 —— 「今天平盘」与「源没给」必须可分辨，
    否则 `0 >= 5%` 永远不触发而且看不出来（同 `_canonical` 的口径）。"""
    rows = {CRYPTO: [{"symbol": "BTCUSDT.BN", "price": 65000.0}]}
    with _patch_fetchers(rows):
        q = qr.fetch_quotes_detailed(["BTCUSDT.BN"])["quotes"][0]
    assert q["change_percent"] is None
    assert q["volume"] is None


# ── 5. 旧签名不变 ────────────────────────────────────────────────────────

def test_fetch_quotes_still_returns_a_plain_list(monkeypatch):
    """四个既有调用方拿的仍是列表 —— 本卡不改它们的签名。"""
    monkeypatch.setattr(qr, "_fetch_a_share",
                        lambda syms: [qr._canonical("600519.SH", price=1650.0)])
    with _patch_fetchers({}):
        out = qr.fetch_quotes(["600519.SH"])
    assert isinstance(out, list) and out[0]["symbol"] == "600519.SH"


def test_empty_input_does_not_touch_the_network():
    with _patch_fetchers({}):
        out = qr.fetch_quotes_detailed([])
    assert out == {"quotes": [], "missing": [], "skipped_closed": [], "by_market": {}}
    assert _FakeFetcher.calls == []


# ── 6. 复审补的回归 ──────────────────────────────────────────────────────

def test_a_share_is_also_skipped_when_closed(monkeypatch):
    """🔴 闭市判定对 A 股同样适用。

    不然周末/盘后每次调用照打腾讯→新浪→百度三源，而且「A 股休市」会被报成
    「取不到行情、本轮**没有被检查**」—— 那句话是错的，还会天天刷 warning。
    """
    called = {"n": 0}

    def _spy(syms):
        called["n"] += 1
        return []

    monkeypatch.setattr(qr, "_fetch_a_share", _spy)
    with _patch_fetchers({}), patch(
            "common.market_session.is_open", lambda m, now=None: False):
        out = qr.fetch_quotes_detailed(["600519.SH"])
    assert called["n"] == 0, "A 股闭市还打了三源"
    assert out["skipped_closed"] == ["600519.SH"]
    assert out["missing"] == []


def test_duplicate_symbols_are_deduped(monkeypatch):
    """同一只票传两次不该发两次请求，也不该在计数里翻倍。"""
    monkeypatch.setattr(qr, "_fetch_a_share",
                        lambda syms: [qr._canonical(s, price=1.0) for s in syms])
    rows = {US_STOCK: [{"symbol": "AAPL", "price": 190.0}]}
    with _patch_fetchers(rows), patch(
            "common.market_session.is_open", lambda m, now=None: True):
        out = qr.fetch_quotes_detailed(
            ["AAPL", "AAPL", "600519.SH", "600519.SH"])
    assert sorted(q["symbol"] for q in out["quotes"]) == ["600519.SH", "AAPL"]
    assert out["by_market"] == {A_SHARE: 1, US_STOCK: 1}
    assert out["missing"] == []
    assert len(_FakeFetcher.calls) == 1 and _FakeFetcher.calls[0][1] == ("AAPL",)


def test_proxy_exhausted_counts_as_missing_not_skipped():
    """🔴 代理耗尽是**取不到**，不是「刻意没取」。

    混进 `skipped_closed` 就等于告诉调用方「这只票不用管」—— 而它其实没被检查。
    """
    from net import ProxyExhaustedError

    def _boom(market, config=None):
        raise ProxyExhaustedError("没 IP 了")

    with patch("acquisition.markets.factory.FetcherFactory.create", _boom), patch(
            "common.market_session.is_open", lambda m, now=None: True):
        out = qr.fetch_quotes_detailed(["AAPL"])
    assert out["missing"] == ["AAPL"]
    assert out["skipped_closed"] == []
