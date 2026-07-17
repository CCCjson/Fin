"""quote_router 多源故障转移的行为测试。

守住四件事：canonical 形状不漂移、逐 symbol 补缺口、缺失标的不烧 IP（百度返空跳过
而非抛异常换 IP）、以及所有源都只经 domestic_rotate 出网（铁律）。
"""
import pytest

from acquisition.markets import quote_router as qr
from net import ProxyExhaustedError

# ---------------- canonical 形状 / 字段映射 ----------------

CANONICAL_KEYS = {
    "symbol", "name", "price", "change", "change_percent", "volume", "amount",
    "open", "high", "low", "prev_close", "amplitude", "turnover", "timestamp",
    "is_index", "source", "as_of",
}


def test_canonical_shape_is_frozen():
    """实盘监控靠这套键；少一个 position_guardian/price_alert 就读到 None。"""
    row = qr._canonical("600519.SH", price=1.0)
    assert set(row.keys()) == CANONICAL_KEYS


def test_missing_numeric_fields_stay_none_not_zero():
    """🔒 P0-2 门禁：源没给的字段必须是 None，**不许用 0 冒充**。

    此前 `change`/`change_percent`/`volume`/`open`/`high`/`low` 都是
    `x if x is not None else 0` —— 「今天平盘」与「源没返回这字段」在归一化第一跳
    就不可分辨。后果不是理论上的：涨幅预警 `change_pct >= 5%` 拿到 0 永远不触发，
    静默失效且看不出来。别为了「形状好看」把这条改回去。
    """
    row = qr._canonical("600519.SH", price=1.0)
    for key in ("change", "change_percent", "volume", "amount", "open", "high", "low"):
        assert row[key] is None, f"{key} 必须留 None，不许用 0 顶替缺失"


def test_as_of_is_source_reported_not_fetch_time():
    """🔒 P0-2 门禁：`as_of`（源自报的报价时刻）与 `timestamp`（我们的抓取时刻）
    是两回事，源没给 as_of 就得是 None。

    实测：17:49 抓到的行，新浪自报报价时刻 15:34 —— 拿 now() 当报价时间等于凭空
    把收盘价说新两小时，正是「数据看着新鲜实际是三天前收盘价」那个病的源头。
    """
    row = qr._canonical("600519.SH", price=1.0)
    assert row["as_of"] is None            # 没传就是不知道，不许拿 timestamp 顶
    assert row["timestamp"] is not None    # 抓取时刻照常有

    row2 = qr._canonical("600519.SH", price=1.0, as_of="2026-07-17T15:34:59",
                         source="sina")
    assert row2["as_of"] == "2026-07-17T15:34:59"
    assert row2["source"] == "sina"
    assert row2["as_of"] != row2["timestamp"]


def test_from_china_batch_maps_pct_and_amount():
    """腾讯/新浪解析器用 change_pct/change_amount，canonical 用 change_percent/change。"""
    src = {"symbol": "600519.SH", "name": "贵州茅台", "price": 1200.0,
           "change_pct": -0.41, "change_amount": -5.0, "prev_close": 1204.98,
           "high": 1210.0, "low": 1190.0}
    out = qr._from_china_batch(src)
    assert out["change_percent"] == -0.41
    assert out["change"] == -5.0
    assert out["prev_close"] == 1204.98
    assert set(out.keys()) == CANONICAL_KEYS


def test_from_china_batch_carries_source_and_quote_time():
    """解析器的 quote_time（源自报报价时刻）要流到 canonical 的 as_of，并带上源名。"""
    src = {"symbol": "600519.SH", "price": 1200.0,
           "quote_time": "2026-07-17T15:34:59"}
    out = qr._from_china_batch(src, "sina")
    assert out["as_of"] == "2026-07-17T15:34:59"
    assert out["source"] == "sina"

    # 源没给报价时刻 → as_of 是 None，不许悄悄拿抓取时刻顶替
    out2 = qr._from_china_batch({"symbol": "600519.SH", "price": 1.0}, "tencent")
    assert out2["as_of"] is None
    assert out2["source"] == "tencent"


def test_index_flag_disambiguates_same_code():
    """000001.SH 是指数、000001.SZ 是个股——is_index 必须分得开。"""
    assert qr._canonical("000001.SH")["is_index"] is True
    assert qr._canonical("000001.SZ")["is_index"] is False
    assert qr._canonical("399006.SZ")["is_index"] is True   # 创业板指


# ---------------- 百度解析 ----------------

_BAIDU_OK = {
    "Result": {
        "cur": {"price": "1199.99", "ratio": "-0.41%", "increase": "-4.99",
                "volume": "57600", "amount": "68954112.00"},
        "pankouinfos": {"list": [
            {"ename": "open", "originValue": "1197.12"},
            {"ename": "high", "originValue": "1210.00"},
            {"ename": "low", "originValue": "1190.00"},
            {"ename": "preClose", "originValue": "1204.98"},
            {"ename": "turnoverRatio", "originValue": "0.31"},
            {"ename": "amplitudeRatio", "originValue": "1.66"},
        ]},
        "basicinfos": {"name": "贵州茅台"},
    }
}


def test_parse_baidu_extracts_full_row():
    row = qr._parse_baidu("600519.SH", _BAIDU_OK)
    assert row is not None
    assert row["name"] == "贵州茅台"
    assert row["price"] == 1199.99
    assert row["change_percent"] == -0.41   # "-0.41%" 去掉 %
    assert row["change"] == -4.99
    assert row["prev_close"] == 1204.98
    assert row["high"] == 1210.0


def test_parse_baidu_returns_none_without_price():
    assert qr._parse_baidu("999999.SZ", {"Result": {"cur": {"price": "0"}}}) is None
    assert qr._parse_baidu("999999.SZ", {}) is None


# ---------------- 假 session：测 runner 出网与容错 ----------------

class _FakeResp:
    def __init__(self, *, text="", json_data=None, raise_json=False):
        self.text = text
        self.encoding = "utf-8"
        self._json = json_data
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json


class _FakeSession:
    """按 url 返回预设响应；记录收到的 proxies（构造时传入）。"""
    def __init__(self, responses, proxies):
        self._responses = responses    # list，按调用顺序弹出
        self.proxies_seen = proxies
        self.closed = False

    def get(self, url, **kwargs):
        return self._responses.pop(0)

    def close(self):
        self.closed = True


def _patch_session(monkeypatch, responses):
    captured = {}

    def _factory(proxies=None):
        sess = _FakeSession(list(responses), proxies)
        captured["session"] = sess
        return sess

    monkeypatch.setattr(qr, "make_domestic_session", _factory)
    return captured


def test_baidu_skips_bad_json_without_raising(monkeypatch):
    """核心 IP 保护：某只码百度返空 → 跳过，runner 正常返回，绝不抛异常触发换 IP。

    这条守着刚修的回归——此前 resp.json() 的 ValueError 被 _rotate 当网络失败，
    为一个退市/无效码买了 IP。
    """
    _patch_session(monkeypatch, [_FakeResp(raise_json=True)])
    rows = qr._run_baidu(["999999.SZ"], None)   # 不抛异常
    assert rows == []


def test_baidu_network_error_propagates(monkeypatch):
    """网络真失败（session.get 抛 RequestException）必须往上抛 → _rotate 换 IP。"""
    import requests

    class _Boom:
        def get(self, *a, **k):
            raise requests.RequestException("boom")

        def close(self):
            pass

    monkeypatch.setattr(qr, "make_domestic_session", lambda proxies=None: _Boom())
    with pytest.raises(requests.RequestException):
        qr._run_baidu(["600519.SH"], None)


# ---------------- 指数快照（四胞胎合一） ----------------

_ULIST_OK = {"rc": 0, "data": {"diff": [
    {"f12": "000001", "f14": "上证指数", "f2": 3964.71, "f3": -0.79,
     "f4": -31.45, "f6": 3.8e11},
]}}


def test_index_snapshot_parses_canonical_row(monkeypatch):
    _patch_session(monkeypatch, [_FakeResp(json_data=_ULIST_OK)])
    rows = qr.fetch_index_snapshot(["1.000001"])
    assert rows == [{"code": "000001", "name": "上证指数", "price": 3964.71,
                     "change_pct": -0.79, "change_amount": -31.45, "amount": 3.8e11}]


def test_index_snapshot_empty_secids_no_request():
    assert qr.fetch_index_snapshot([]) == []


# ---------------- 阶梯：逐 symbol 补缺口 ----------------

def _fake_rotate_direct(run, **kwargs):
    """把 domestic_rotate 换成「只跑直连（proxy=None）一轮」，隔离网络。"""
    return run(None)


def test_ladder_gap_fills_across_sources(monkeypatch):
    """腾讯给 A、新浪给 B、百度给 C —— 三源各补一块，最终三只都在。"""
    monkeypatch.setattr(qr, "domestic_rotate", _fake_rotate_direct)
    monkeypatch.setattr(qr, "_SOURCES", [
        ("腾讯", lambda syms, px: [qr._canonical("600519.SH", price=1)]),
        ("新浪", lambda syms, px: [qr._canonical("000001.SZ", price=2)]),
        ("百度", lambda syms, px: [qr._canonical("300750.SZ", price=3)]),
    ])
    out = qr.fetch_quotes(["600519.SH", "000001.SZ", "300750.SZ"])
    assert {r["symbol"] for r in out} == {"600519.SH", "000001.SZ", "300750.SZ"}


def test_ladder_short_circuits_when_complete(monkeypatch):
    """腾讯一把拿全 → 新浪/百度不该被调用（省请求、省 IP）。"""
    monkeypatch.setattr(qr, "domestic_rotate", _fake_rotate_direct)
    calls = []

    def _sina(syms, px):
        calls.append("sina")
        return []

    monkeypatch.setattr(qr, "_SOURCES", [
        ("腾讯", lambda syms, px: [qr._canonical(s, price=1) for s in syms]),
        ("新浪", _sina),
    ])
    out = qr.fetch_quotes(["600519.SH", "000001.SZ"])
    assert len(out) == 2
    assert calls == []          # 新浪没被碰


def test_ladder_only_asks_each_source_for_whats_missing(monkeypatch):
    """第二源只该收到第一源没拿到的 symbol。"""
    monkeypatch.setattr(qr, "domestic_rotate", _fake_rotate_direct)
    seen = {}

    def _tencent(syms, px):
        seen["tencent"] = list(syms)
        return [qr._canonical("600519.SH", price=1)]

    def _sina(syms, px):
        seen["sina"] = list(syms)
        return [qr._canonical("000001.SZ", price=2)]

    monkeypatch.setattr(qr, "_SOURCES", [("腾讯", _tencent), ("新浪", _sina)])
    qr.fetch_quotes(["600519.SH", "000001.SZ"])
    assert seen["tencent"] == ["000001.SZ", "600519.SH"]   # sorted
    assert seen["sina"] == ["000001.SZ"]                   # 腾讯拿到的 600519 不再问


def test_missing_symbol_returns_partial_not_raise(monkeypatch):
    """全源都没有的标的 → 返回已拿到的部分，不抛异常（调用方按空=重试处理）。"""
    monkeypatch.setattr(qr, "domestic_rotate", _fake_rotate_direct)
    monkeypatch.setattr(qr, "_SOURCES", [
        ("腾讯", lambda syms, px: [qr._canonical("600519.SH", price=1)]),
    ])
    out = qr.fetch_quotes(["600519.SH", "999999.SZ"])
    assert [r["symbol"] for r in out] == ["600519.SH"]


def test_proxy_exhausted_falls_through_to_next_source(monkeypatch):
    """某源直连失败且代理耗尽（ProxyExhaustedError）→ 换下一源，不炸整个调用。"""
    def _rotate(run, *, what, **kwargs):
        if "腾讯" in what:
            raise ProxyExhaustedError("no ip")
        return run(None)

    monkeypatch.setattr(qr, "domestic_rotate", _rotate)
    monkeypatch.setattr(qr, "_SOURCES", [
        ("腾讯", lambda syms, px: [qr._canonical("600519.SH", price=99)]),
        ("新浪", lambda syms, px: [qr._canonical("600519.SH", price=1)]),
    ])
    out = qr.fetch_quotes(["600519.SH"])
    assert len(out) == 1
    assert out[0]["price"] == 1       # 来自新浪，腾讯那轮被跳过


def test_non_a_share_symbols_ignored(monkeypatch):
    """HK/US 不归本 router 管。"""
    monkeypatch.setattr(qr, "domestic_rotate", _fake_rotate_direct)
    monkeypatch.setattr(qr, "_SOURCES", [("腾讯", lambda syms, px: [])])
    assert qr.fetch_quotes(["00700.HK", "AAPL"]) == []


def test_every_source_goes_through_domestic_rotate(monkeypatch):
    """铁律：没有任何源绕过 domestic_rotate 直接出网。"""
    routed = []

    def _spy_rotate(run, *, what, prefer_direct, **kwargs):
        routed.append((what, prefer_direct))
        return []          # 都没拿到 → 逼着走完整条链

    monkeypatch.setattr(qr, "domestic_rotate", _spy_rotate)
    qr.fetch_quotes(["600519.SH"])
    assert [w for w, _ in routed] == ["实时行情/腾讯", "实时行情/新浪", "实时行情/百度"]
    assert all(pd is True for _, pd in routed)   # 全部 prefer_direct
