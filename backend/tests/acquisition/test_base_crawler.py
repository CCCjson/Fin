"""BaseCrawler 门面：两条铁律（有上限下载 + 国内只换 IP）必须同时生效。

这些测试钉住的是**结构**而非某一行实现：只要有人给 acquisition 加一条绕过
`net.domestic_*` 的国内抓取路径，或者把 bounded 拆掉换成裸 `.json()`，这里会红。
"""
import pytest

from acquisition import BaseCrawler, Channel
from net.domestic import ProxyExhaustedError

pytestmark = pytest.mark.baseline


class _FakeProxy:
    is_expired = False

    def __init__(self, ip: str):
        self.ip, self.port = ip, 8080

    def to_requests_proxies(self):
        return {"http": f"http://{self.ip}:8080", "https": f"http://{self.ip}:8080"}

    def to_env_url(self):
        return f"http://{self.ip}:8080"


class _FakePM:
    """ips 为空 = 额度耗尽。`switch_proxy(stale=)` 忠实建模「别人换过了就白捡」。"""

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

    def switch_proxy(self, stale=None):
        if (stale is not None and self.current_proxy is not None
                and self.current_proxy is not stale
                and not self.current_proxy.is_expired):
            return self.current_proxy
        return self._next()

    def get_proxy(self):
        return self.current_proxy or self._next()


class _Resp:
    def __init__(self, body: bytes, status: int = 200, headers=None):
        self._body, self.status_code = body, status
        self.headers = headers or {}

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


def _patch_domestic(monkeypatch, resp, calls):
    import net.domestic as dom

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def request(self, method, url, **kw):
            calls.append(self.proxies)
            return resp

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))


# ── 铁律：DOMESTIC 通道额度耗尽时一个请求都不发 ───────────────────────────────

def test_domestic_channel_inherits_the_iron_rule(monkeypatch):
    import net.domestic as dom

    calls = []
    _patch_domestic(monkeypatch, _Resp(b"{}"), calls)
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=[]))

    crawler = BaseCrawler(channel=Channel.DOMESTIC)
    with pytest.raises(ProxyExhaustedError):
        crawler.get_json("https://push2.eastmoney.com/api/qt/ulist.np/get")
    assert calls == [], "额度耗尽时发出了请求 —— 那就是降级直连"


def test_domestic_channel_uses_proxy_when_available(monkeypatch):
    import net.domestic as dom

    calls = []
    _patch_domestic(monkeypatch, _Resp(b'{"rc": 0}'), calls)
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=["1.1.1.1"]))

    data = BaseCrawler(channel=Channel.DOMESTIC).get_json("https://push2.eastmoney.com/x")
    assert data == {"rc": 0}
    assert calls == [{"http": "http://1.1.1.1:8080", "https": "http://1.1.1.1:8080"}]


# ── 铁律：响应封顶 ──────────────────────────────────────────────────────────

def test_truncated_response_marks_json_unusable(monkeypatch):
    import net.domestic as dom

    calls = []
    _patch_domestic(monkeypatch, _Resp(b'{"a": ' + b"1" * 5000), calls)
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=["1.1.1.1"]))

    cap = BaseCrawler(channel=Channel.DOMESTIC).get("https://push2.eastmoney.com/x", max_bytes=100)
    assert cap.truncated is True and cap.usable_json is False


def test_get_json_refuses_to_parse_an_incomplete_body(monkeypatch):
    """超上限的响应宁可返回 None，也不能把半截 body 喂给 json.loads。"""
    import net.domestic as dom

    calls = []
    huge = _Resp(b'{"a": 1}', headers={"content-length": "999999999"})
    _patch_domestic(monkeypatch, huge, calls)
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: _FakePM(ips=["1.1.1.1"]))

    crawler = BaseCrawler(channel=Channel.DOMESTIC)
    assert crawler.get_json("https://push2.eastmoney.com/x") is None, "超限响应不该被解析"
    assert len(calls) == 1, "超限是答案不是失败，不该换 IP 重试"


def test_overseas_channel_caps_response(monkeypatch):
    import acquisition.crawler.base as base

    resp = _Resp(b"z" * 5000)

    class _Sess:
        def request(self, method, url, **kw):
            return resp

    monkeypatch.setattr(base, "make_session", lambda ch, impersonate=False: _Sess())

    cap = BaseCrawler(channel=Channel.OVERSEAS).get("https://example.com", max_bytes=1000)
    assert cap.truncated is True and len(cap.data) == 1000


def test_overseas_failure_returns_none_no_ip_to_rotate(monkeypatch):
    """海外通道没有 IP 可换，失败即失败，不该抛 ProxyExhaustedError。"""
    import acquisition.crawler.base as base

    class _Sess:
        def request(self, method, url, **kw):
            raise RuntimeError("DNS 解析失败")

    monkeypatch.setattr(base, "make_session", lambda ch, impersonate=False: _Sess())
    assert BaseCrawler(channel=Channel.OVERSEAS).get("https://example.com") is None


# ── 通道是必答题 ────────────────────────────────────────────────────────────

def test_channel_is_required():
    with pytest.raises(TypeError):
        BaseCrawler()          # type: ignore[call-arg]


def test_domestic_rejects_impersonate():
    """DOMESTIC 逐轮换 IP，session 由 net 层造，塞不进 curl_cffi 指纹。"""
    with pytest.raises(ValueError, match="impersonate"):
        BaseCrawler(channel=Channel.DOMESTIC, impersonate=True)


def test_prefer_direct_defaults_to_false():
    """忘了传参不能偷偷变成直连。"""
    assert BaseCrawler(channel=Channel.DOMESTIC).prefer_direct is False
