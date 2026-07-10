"""铁律：国内抓取失败只换快代理 IP 重试，**任何场景禁止降级本地直连**。

来源：commit `7158f37`「国内抓取失败一律换代理IP重试，彻底移除直连降级」，
理由是东财高频接口直连会被服务端掐断（`ProtocolError Connection aborted`），
一直失败——直连不是兜底，是无效重试。

## 但代码曾经在撒谎

`domestic_get` 的 docstring 写着「绝不直连兜底」，实际是：

    p = pm.fetch_one_proxy() if attempt == 0 else pm.switch_proxy()
    if p:
        proxies = p.to_requests_proxies()
    session = make_domestic_session(proxies)   # 取不到 IP → proxies=None → 直连

快代理额度耗尽时 `fetch_one_proxy()` 返回 None，于是**每一轮都在直连**。
`domestic_akshare` 同理（`proxy_env(None)` 就是直连）。

## 现在的语义（2026-07-10 Jason 拍板「算，硬失败」）

| 状态 | 判定 | 行为 |
|---|---|---|
| `.env` 没配快代理（`pm is None`）| 没有代理服务可用 | 直连是**唯一选项**，不是降级 → 允许 |
| 配了但取不到 IP（额度耗尽/API 挂）| 降级直连 | **抛 `ProxyExhaustedError`**，绝不发请求 |
| 配了、拿到 IP、请求失败 | 换下一个 IP | 重试到 `max_rounds` 耗尽再抛 |
"""
import pytest

from net.domestic import ProxyExhaustedError

pytestmark = pytest.mark.baseline


class _FakeProxy:
    def __init__(self, ip: str):
        self.ip, self.port = ip, 8080

    def to_requests_proxies(self):
        return {"http": f"http://{self.ip}:8080", "https": f"http://{self.ip}:8080"}

    def to_env_url(self):
        return f"http://{self.ip}:8080"


class _FakePM:
    """模拟快代理：ips 为空 = 额度耗尽。

    `get_proxy()` 复用当前 IP（真实 ProxyManager 的语义）；这里的 fake IP 永不过期，
    所以「首轮 get_proxy → 失败 → switch_proxy」的换 IP 路径能被如实测出来。
    """

    def __init__(self, ips=()):
        self.ips = list(ips)
        self.api_url = "https://dps.kdlapi.com/fake"
        self.handed_out = []
        self.current_proxy = None

    def _next(self):
        if not self.ips:
            self.current_proxy = None
            return None
        ip = self.ips.pop(0)
        self.handed_out.append(ip)
        self.current_proxy = _FakeProxy(ip)
        return self.current_proxy

    fetch_one_proxy = _next
    switch_proxy = _next

    def get_proxy(self):
        return self.current_proxy or self._next()


@pytest.fixture
def spy_session(monkeypatch):
    """记录每次 requests 请求用的 proxies。proxies=None 就是直连。"""
    import net.domestic as dom

    calls = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def get(self, url, **kw):
            calls.append(self.proxies)
            return _Resp()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))
    return calls


# ── domestic_get ──────────────────────────────────────────────────────────

def test_raises_instead_of_going_direct_when_quota_exhausted(monkeypatch, spy_session):
    """配了快代理但一个 IP 都取不到 → 一个请求都不许发出去。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=[]))
    with pytest.raises(ProxyExhaustedError):
        dom.domestic_get("https://push2.eastmoney.com/api/qt/ulist.np/get", max_rounds=3)
    assert spy_session == [], "额度耗尽时不该发出任何请求（发了就是直连）"


def test_direct_is_legal_when_no_proxy_configured(monkeypatch, spy_session):
    """`.env` 没配快代理时 `get_proxy_manager()` 返回 None —— 直连是唯一选项。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: None)
    resp = dom.domestic_get("https://push2.eastmoney.com/x")
    assert resp is not None
    assert spy_session == [None], "未配代理时应当直连一次"


def test_uses_proxy_and_rotates_ip_on_failure(monkeypatch):
    """拿得到 IP 就用；请求失败换下一个 IP，绝不回退直连。"""
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1", "2.2.2.2", "3.3.3.3"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    seen = []

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def get(self, url, **kw):
            seen.append(self.proxies)
            raise RuntimeError("Connection aborted")

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    with pytest.raises(ProxyExhaustedError):
        dom.domestic_get("https://push2.eastmoney.com/x", max_rounds=3)

    assert len(seen) == 3, "三轮都发了请求（每轮都有 IP 可用）"
    assert None not in seen, "没有任何一轮走直连"
    assert pm.handed_out == ["1.1.1.1", "2.2.2.2", "3.3.3.3"], "失败一次换一个 IP"


def test_partial_exhaustion_still_never_goes_direct(monkeypatch):
    """先给一个 IP、后续取不到 —— 后续轮次必须跳过，不能退化成直连。"""
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    seen = []

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def get(self, url, **kw):
            seen.append(self.proxies)
            raise RuntimeError("boom")

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    with pytest.raises(ProxyExhaustedError):
        dom.domestic_get("https://push2.eastmoney.com/x", max_rounds=3)
    assert seen == [{"http": "http://1.1.1.1:8080", "https": "http://1.1.1.1:8080"}], \
        "只有拿到 IP 的那一轮发了请求；后续取不到 IP 立即中止，绝不直连"


# ── domestic_bounded_get：两条铁律合一（换 IP + 响应封顶）─────────────────────
#
# 13.4-2 新增。它和 domestic_get 共用 `_rotate` 驱动，所以铁律自动继承；这里钉住的是
# 「共用」这件事本身——将来谁把 bounded 路径改成自己手写循环，这几条会立刻变红。

@pytest.fixture
def spy_bounded(monkeypatch):
    """记录每次 bounded 请求用的 session.proxies。空 proxies 就是直连。"""
    import net.domestic as dom

    calls = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def iter_content(self, chunk_size=65536):
            yield b'{"ok": true}'

        def close(self):
            pass

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies or {}

        def request(self, method, url, **kw):
            calls.append(self.proxies or None)
            return _Resp()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))
    return calls


def test_bounded_raises_instead_of_going_direct_when_quota_exhausted(monkeypatch, spy_bounded):
    """封顶路径同样不许在额度耗尽时偷偷直连。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=[]))
    with pytest.raises(ProxyExhaustedError):
        dom.domestic_bounded_get("https://push2.eastmoney.com/x", max_rounds=3)
    assert spy_bounded == [], "额度耗尽时不该发出任何请求"


def test_bounded_returns_capped_and_caps_the_body(monkeypatch):
    """拿到 IP 后正常返回 Capped，且响应体确实被封顶截断。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=["1.1.1.1"]))

    class _Resp:
        status_code = 200
        headers = {}

        def iter_content(self, chunk_size=65536):
            yield b"x" * 5000

        def close(self):
            pass

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def request(self, method, url, **kw):
            return _Resp()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    cap = dom.domestic_bounded_get("https://push2.eastmoney.com/x", max_bytes=1000)
    assert cap is not None
    assert cap.truncated is True and len(cap.data) == 1000
    assert cap.usable_json is False, "截断的 JSON 不可解析"


def test_bounded_rotates_ip_on_http_error(monkeypatch):
    """4xx/5xx 触发换 IP（与 domestic_get 的 raise_for_status 对齐），绝不直连。"""
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1", "2.2.2.2", "3.3.3.3"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    seen = []

    class _Resp:
        status_code = 503
        headers = {}

        def iter_content(self, chunk_size=65536):
            yield b""

        def close(self):
            pass

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def request(self, method, url, **kw):
            seen.append(self.proxies)
            return _Resp()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    with pytest.raises(ProxyExhaustedError):
        dom.domestic_bounded_get("https://push2.eastmoney.com/x", max_rounds=3)
    assert len(seen) == 3 and None not in seen
    assert pm.handed_out == ["1.1.1.1", "2.2.2.2", "3.3.3.3"], "每个 5xx 换一个 IP"


def test_bounded_too_large_is_an_answer_not_a_failure(monkeypatch):
    """Content-Length 预判超限 → 直接返回，不换 IP（换了那份响应照样超限）。"""
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1", "2.2.2.2"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    class _Resp:
        status_code = 200
        headers = {"content-length": "999999999"}

        def iter_content(self, chunk_size=65536):
            raise AssertionError("超限就不该开始下载")

        def close(self):
            pass

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def request(self, method, url, **kw):
            return _Resp()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    cap = dom.domestic_bounded_get("https://push2.eastmoney.com/x", max_bytes=1000, max_rounds=3)
    assert cap is not None and cap.too_large is True
    assert pm.handed_out == ["1.1.1.1"], "只买了一个 IP，没有徒劳换 IP 重试"


def test_bounded_prefer_direct_tries_direct_first(monkeypatch, spy_bounded):
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)
    dom.domestic_bounded_get("https://push2.eastmoney.com/x", prefer_direct=True)
    assert spy_bounded == [None], "第一轮必须直连"
    assert pm.handed_out == [], "直连成功就不该买 IP"


# ── domestic_json 透传异常 ─────────────────────────────────────────────────

def test_domestic_json_propagates_exhaustion(monkeypatch, spy_session):
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=[]))
    with pytest.raises(ProxyExhaustedError):
        dom.domestic_json("https://push2.eastmoney.com/x")


# ── domestic_akshare ──────────────────────────────────────────────────────

def test_akshare_never_runs_direct_when_quota_exhausted(monkeypatch):
    """akshare 内部走 requests 读 env 代理。proxy_env(None) 就是直连。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=[]))
    ran = []

    def _fake_ak():
        ran.append(1)
        return "data"

    _fake_ak.__name__ = "stock_zh_a_spot_em"
    with pytest.raises(ProxyExhaustedError):
        dom.domestic_akshare(_fake_ak, max_rounds=3)
    assert ran == [], "额度耗尽时不该真的去调 akshare（那就是直连）"


def test_akshare_direct_is_legal_when_no_proxy_configured(monkeypatch):
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: None)

    def _fake_ak():
        return "data"

    _fake_ak.__name__ = "stock_zh_a_spot_em"
    assert dom.domestic_akshare(_fake_ak) == "data"


def test_proxy_exhausted_error_is_exported_from_net():
    """调用方要能 `from net import ProxyExhaustedError` 来接。"""
    import net

    assert net.ProxyExhaustedError is ProxyExhaustedError


# ── 逆向爬虫栈（严格统一：含登录也绝不直连）─────────────────────────────────

class _FakeProxyInfo:
    protocol, ip, port = "http", "9.9.9.9", 8080
    username = password = None
    url = "http://9.9.9.9:8080"


@pytest.fixture
def _reset_proxy_route(monkeypatch):
    import knowledge_engine.browser.proxy_route as pr
    monkeypatch.setattr(pr, "_manager", None)
    return pr


def test_reverse_crawler_raises_instead_of_direct(monkeypatch, _reset_proxy_route):
    """股吧/雪球取不到 IP → 抛，不返回 None（None 会被上游当直连）。"""
    pr = _reset_proxy_route
    monkeypatch.setattr(pr, "get_scraper_proxy_enabled", lambda: True)
    monkeypatch.setattr(pr, "_get_manager", lambda: type("M", (), {"get_proxy": lambda s: None})())

    for url in ("https://guba.eastmoney.com/list,600519.html", "https://xueqiu.com/S/SH600519"):
        with pytest.raises(ProxyExhaustedError):
            pr.curl_proxy_for(url)
        with pytest.raises(ProxyExhaustedError):
            pr.playwright_proxy_for(url)


def test_manual_login_also_bound_by_the_rule(monkeypatch, _reset_proxy_route):
    """manual_login 走 playwright_proxy_for —— 严格统一后登录也不许直连。"""
    pr = _reset_proxy_route
    monkeypatch.setattr(pr, "get_scraper_proxy_enabled", lambda: True)
    monkeypatch.setattr(pr, "_get_manager", lambda: type("M", (), {"get_proxy": lambda s: None})())
    with pytest.raises(ProxyExhaustedError):
        pr.playwright_proxy_for("https://xueqiu.com/")


def test_explicit_opt_out_is_the_only_legal_direct(monkeypatch, _reset_proxy_route):
    """KNOWLEDGE_SCRAPER_PROXY_ENABLED=false 是明示选择直连，不是静默降级。"""
    pr = _reset_proxy_route
    monkeypatch.setattr(pr, "get_scraper_proxy_enabled", lambda: False)
    assert pr.curl_proxy_for("https://guba.eastmoney.com/x") is None
    assert pr.playwright_proxy_for("https://xueqiu.com/") is None


def test_proxy_enabled_defaults_to_true():
    """默认关（旧行为）意味着逆向爬虫默认直连——与铁律相背。"""
    import os

    from knowledge_engine.config import get_scraper_proxy_enabled
    os.environ.pop("KNOWLEDGE_SCRAPER_PROXY_ENABLED", None)
    assert get_scraper_proxy_enabled() is True


def test_overseas_sites_unaffected(monkeypatch, _reset_proxy_route):
    """铁律只管国内。CapitalIQ 走海外出口（直连优先，不通走 Shadowrocket）。"""
    pr = _reset_proxy_route
    monkeypatch.setattr(pr, "resolve_overseas", lambda: None)
    assert pr.curl_proxy_for("https://capitaliq.spglobal.com/x") is None
    monkeypatch.setattr(pr, "get_scraper_proxy_enabled", lambda: True)
    monkeypatch.setattr(pr, "_get_manager", lambda: type("M", (), {"get_proxy": lambda s: None})())
    assert pr.curl_proxy_for("https://capitaliq.spglobal.com/x") is None   # 不抛


# ── prefer_direct：显式的「先本地，不行再换快代理」（新闻路径）──────────────
#
# Jason 2026-07-10：新闻接口先走本地直连，失败再上快代理。这**不违反铁律**——
# 铁律禁止的是「取不到 IP 就悄悄用 proxies=None 发请求」。这里直连是第 0 次
# 尝试，是调用方显式点的头，且一旦直连失败就必须走代理；代理也取不到 → 照样抛。

def test_prefer_direct_tries_direct_first(monkeypatch, spy_session):
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)
    dom.domestic_get("https://push2.eastmoney.com/x", prefer_direct=True)
    assert spy_session == [None], "第一轮必须直连"
    assert pm.handed_out == [], "直连成功就不该买 IP"


def test_prefer_direct_falls_forward_to_proxy_on_failure(monkeypatch):
    """直连失败 → 换快代理（前进，不是后退）。"""
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1", "2.2.2.2"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    seen = []

    class _Sess:
        def __init__(self, proxies): self.proxies = proxies

        def get(self, url, **kw):
            seen.append(self.proxies)
            if self.proxies is None:
                raise RuntimeError("直连被掐断")

            class _R:
                status_code = 200

                def raise_for_status(self): pass
            return _R()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    dom.domestic_get("https://push2.eastmoney.com/x", prefer_direct=True, max_rounds=3)
    assert seen[0] is None, "先直连"
    assert seen[1] == {"http": "http://1.1.1.1:8080", "https": "http://1.1.1.1:8080"}, "再代理"
    assert len(seen) == 2, "代理成功就停"


def test_prefer_direct_still_raises_when_proxy_exhausted(monkeypatch):
    """直连失败 + 取不到 IP → 抛。绝不允许「再试一次直连」。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=[]))

    seen = []

    class _Sess:
        def __init__(self, proxies): self.proxies = proxies

        def get(self, url, **kw):
            seen.append(self.proxies)
            raise RuntimeError("直连被掐断")

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    with pytest.raises(ProxyExhaustedError):
        dom.domestic_get("https://push2.eastmoney.com/x", prefer_direct=True, max_rounds=3)
    assert seen == [None], "只有第 0 轮那一次显式直连；之后取不到 IP 就中止"


def test_prefer_direct_defaults_to_false(monkeypatch, spy_session):
    """默认必须是「只走代理」。忘了传参不能偷偷变成直连。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=[]))
    with pytest.raises(ProxyExhaustedError):
        dom.domestic_get("https://push2.eastmoney.com/x")
    assert spy_session == []


def test_akshare_prefer_direct(monkeypatch):
    """新闻的 ak.stock_news_em 走这条路。"""
    import net.domestic as dom

    pm = _FakePM(ips=["1.1.1.1"])
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    def _ak(): return "data"
    _ak.__name__ = "stock_news_em"
    assert dom.domestic_akshare(_ak, prefer_direct=True) == "data"
    assert pm.handed_out == [], "直连成功就不该买 IP"
