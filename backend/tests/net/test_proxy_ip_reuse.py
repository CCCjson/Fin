"""快代理 IP 消耗：同一个 IP 在有效期内必须复用。

## 事故

`ProxyManager.get_proxy()` 本来就有「当前 IP 没过期就直接返回」的复用逻辑，
但**全仓没人走它**：

- `net/domestic.py::get_proxy_manager()` 每次调用都 `ProxyManager()` 新建实例
  → `current_proxy` 永远是 None
- `domestic_get` / `domestic_akshare` 调的是 `fetch_one_proxy()`（无条件打提取 API）

结果：**每一次国内请求 = 提取一个全新 IP**。实测同一秒内连打 10 次 `domestic_json`
消耗 10 个 IP。快代理额度 8394 → 0 只用了 8 天（约 1050 次/天）。

## 现在的语义

| 时机 | 方法 | 效果 |
|---|---|---|
| 首轮 | `get_proxy()` | 缓存 IP 没过期（默认 5 分钟）就复用，不打 API |
| 请求失败后换 IP | `switch_proxy()` | 当前 IP 大概率被封，换新的 |
| 取不到 IP | 立即中止 | 不再把 `max_rounds` 轮全打完（额度耗尽时那是白白轰炸提取 API）|
"""
from datetime import datetime, timedelta

import pytest

from net.domestic import ProxyExhaustedError

pytestmark = pytest.mark.baseline


class _CountingPM:
    """记录打了几次提取 API。模拟真实 ProxyManager 的复用语义。"""

    def __init__(self, quota: int = 100, ttl_minutes: int = 5):
        from net.proxy_manager import ProxyInfo

        self.api_url = "https://dps.kdlapi.com/fake"
        self.api_calls = 0
        self.quota = quota
        self.ttl = ttl_minutes
        self.current_proxy = None
        self._ProxyInfo = ProxyInfo

    def fetch_one_proxy(self):
        self.api_calls += 1
        if self.quota <= 0:
            self.current_proxy = None
            return None
        self.quota -= 1
        self.current_proxy = self._ProxyInfo(
            ip=f"10.0.0.{self.api_calls}", port=8080, protocol="http",
            username="u", password="p",
            expire_at=(datetime.now() + timedelta(minutes=self.ttl)).isoformat())
        return self.current_proxy

    def get_proxy(self):
        if self.current_proxy and not self.current_proxy.is_expired:
            return self.current_proxy
        return self.fetch_one_proxy()

    def switch_proxy(self):
        self.current_proxy = None
        return self.fetch_one_proxy()


@pytest.fixture
def ok_session(monkeypatch):
    import net.domestic as dom

    class _Resp:
        status_code = 200

        def raise_for_status(self): pass

        def json(self): return {"ok": True}

    class _Sess:
        def __init__(self, proxies): self.proxies = proxies

        def get(self, url, **kw): return _Resp()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))


# ── 单例 ──────────────────────────────────────────────────────────────────

def test_get_proxy_manager_is_a_singleton(monkeypatch):
    """每次 new 一个 manager，缓存的 current_proxy 就永远丢失。"""
    import net.domestic as dom

    monkeypatch.setenv("kuaidaili_api", "https://fake")
    dom.reset_proxy_manager()
    a, b = dom.get_proxy_manager(), dom.get_proxy_manager()
    assert a is b


def test_singleton_is_none_when_not_configured(monkeypatch):
    import net.domestic as dom

    monkeypatch.delenv("kuaidaili_api", raising=False)
    dom.reset_proxy_manager()
    assert dom.get_proxy_manager() is None


# ── 复用 ──────────────────────────────────────────────────────────────────

def test_ten_requests_share_one_ip(monkeypatch, ok_session):
    """同一秒内连打 10 次，应该只提取 1 个 IP（此前是 10 个）。"""
    import net.domestic as dom

    pm = _CountingPM()
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    for _ in range(10):
        assert dom.domestic_get("https://push2.eastmoney.com/x") is not None
    assert pm.api_calls == 1, f"提取了 {pm.api_calls} 个 IP，应该只提取 1 个"


def test_expired_ip_triggers_exactly_one_new_fetch(monkeypatch, ok_session):
    import net.domestic as dom

    pm = _CountingPM(ttl_minutes=5)
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    dom.domestic_get("https://x/y")
    assert pm.api_calls == 1

    pm.current_proxy.expire_at = (datetime.now() - timedelta(seconds=1)).isoformat()
    dom.domestic_get("https://x/y")
    assert pm.api_calls == 2


def test_failure_rotates_ip_but_success_does_not(monkeypatch):
    """请求失败才换 IP；成功就一直用同一个。"""
    import net.domestic as dom

    pm = _CountingPM()
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    fail_first = {"n": 0}

    class _Sess:
        def __init__(self, proxies): self.proxies = proxies

        def get(self, url, **kw):
            fail_first["n"] += 1
            if fail_first["n"] == 1:
                raise RuntimeError("Connection aborted")

            class _R:
                def raise_for_status(self): pass
            return _R()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    dom.domestic_get("https://x/y", max_rounds=3)
    assert pm.api_calls == 2, "首轮 1 个 + 失败后 switch 1 个"


def test_akshare_also_reuses_the_ip(monkeypatch):
    import net.domestic as dom

    pm = _CountingPM()
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    def _ak():
        return "df"

    _ak.__name__ = "stock_news_em"
    for _ in range(10):
        assert dom.domestic_akshare(_ak) == "df"
    assert pm.api_calls == 1, f"10 只票的新闻抓取提取了 {pm.api_calls} 个 IP"


# ── 额度耗尽时别轰炸提取 API ────────────────────────────────────────────────

def test_exhausted_pool_stops_hammering_the_extraction_api(monkeypatch, ok_session):
    """取不到 IP 就立即中止：`max_rounds=5` 轮全打完 = 白白轰炸 5 次提取 API。"""
    import net.domestic as dom

    pm = _CountingPM(quota=0)
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    with pytest.raises(ProxyExhaustedError):
        dom.domestic_get("https://x/y", max_rounds=5)
    assert pm.api_calls == 1, f"额度耗尽时打了 {pm.api_calls} 次提取 API，应该只打 1 次"


def test_akshare_exhausted_also_stops_early(monkeypatch):
    import net.domestic as dom

    pm = _CountingPM(quota=0)
    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    def _ak(): return "df"
    _ak.__name__ = "x"
    with pytest.raises(ProxyExhaustedError):
        dom.domestic_akshare(_ak, max_rounds=5)
    assert pm.api_calls == 1
