"""一个 IP 服务所有国内抓取 —— 共享靠单例，省钱靠 stale 守卫，原子靠 domestic_rotate。

三件事各司其职，别搞混（这份文档就是为了防止再搞混）：

| 想要的性质 | 靠什么 | 何时生效 |
|---|---|---|
| 东财 / 个股 K 线 / 股吧 / 雪球 **共用一个 IP** | 单例 + `get_proxy()` 复用 | **默认**，IP 没过期就复用 |
| 并发失败时不各买各的替补 | `switch_proxy(stale=手上那个IP)` | 本文件修的 |
| 一次逻辑取数拆成 N 个请求要**整体重试** | `domestic_rotate(run)` | 调用方显式选用 |

## 曾经的病：并发超买

`switch_proxy()` 是**无条件买新 IP**。N 个并发请求撞死在**同一个** IP 上，
就各自调一次 `switch_proxy()` → 买 N 个 IP，尽管它们死的是同一个 IP。

    thread1: get_proxy() → IP1 ─┐
    thread2: get_proxy() → IP1 ─┼─ IP1 死了
    thread3: get_proxy() → IP1 ─┘
    thread1: switch_proxy() → IP2   ← 买
    thread2: switch_proxy() → IP3   ← 又买（明明 IP2 已经是新的了）
    thread3: switch_proxy() → IP4   ← 再买

八天烧掉 8394 个 IP 的账单里，这是其中一层。

## 修法：`switch_proxy(stale=手上那个IP)`

调用方失败时说的是「换掉**我手上这个**」，而不是「随便买一个」。若此刻
`current_proxy` 已经不是它了（别人先一步换过），直接复用那个新 IP，不扣额度。
全程持 `_lock`，所以是确定性的：第一个进去的人买，后面的人白捡。

**这条守卫不认识站点**——它作用在 IP 层。所以东财 + 个股 + 雪球 + akshare
一起并发撞死同一个 IP，也只买一个替补（`test_cross_site_...` 钉住这点）。

## `domestic_rotate` 管的不是省 IP，是原子性

有了 stale 守卫，N 个独立 `domestic_get` 并发失败也只买 1 个替补。
`domestic_rotate` 存在的理由是**整体重试**：5 个子请求凑成一份快照，
不能 3 个来自旧 IP、2 个来自新 IP。代价是重做已成功的子请求。
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from net.domestic import ProxyExhaustedError, domestic_rotate
from net.proxy_manager import ProxyManager

pytestmark = pytest.mark.baseline


class _FakeApi:
    """假的快代理提取 API：每调一次发一个新 IP，并计数（= 扣一次额度）。"""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, url, timeout=10):
        with self._lock:
            self.calls += 1
            n = self.calls

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                # f_et=1 逗号分组格式；给足 300 秒寿命，不让过期干扰计数
                return {"code": 0, "data": {"proxy_list": [f"10.0.0.{n}:8080,300"]}}

        return _Resp()


@pytest.fixture
def pm(monkeypatch):
    """真 ProxyManager + 假提取 API，`api.calls` 就是真实账单。"""
    api = _FakeApi()

    class _Sess:
        def get(self, url, timeout=10):
            return api(url, timeout=timeout)

    monkeypatch.setattr("net.proxy_manager.make_domestic_session", lambda proxies=None: _Sess())
    monkeypatch.setenv("kuaidaili_api", "https://dps.kdlapi.com/fake")
    m = ProxyManager()
    m.api = api            # 测试用句柄
    return m


def _ip_of(proxies) -> str:
    """从 requests proxies dict 里抠出 IP。"""
    return proxies["http"].split("//")[1].split(":")[0]


# ── 1. 并发独立重试：撞死同一个 IP，只该买一个替补 ────────────────────────────

def test_concurrent_failures_on_same_ip_buy_only_one_replacement(monkeypatch, pm):
    """5 个并发 domestic_get 撞死在 IP1 上 → 总共只该买 2 个 IP（IP1 + 一个替补）。

    修复前：买 6 个（IP1 + 每个线程各买一个替补）。
    """
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    dead = "10.0.0.1"
    seen = []
    seen_lock = threading.Lock()

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def get(self, url, **kw):
            ip = _ip_of(self.proxies)
            with seen_lock:
                seen.append(ip)
            if ip == dead:
                raise RuntimeError("Connection aborted")

            class _R:
                status_code = 200

                def raise_for_status(self):
                    pass
            return _R()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(dom.domestic_get, "https://push2.eastmoney.com/x", max_rounds=4)
                   for _ in range(5)]
        results = [f.result() for f in futures]

    assert all(r is not None for r in results), "5 个请求都该最终成功"
    assert pm.api.calls == 2, (
        f"买了 {pm.api.calls} 个 IP。5 个线程死在同一个 IP 上，只该买 1 个替补。"
        f"实际用过的 IP 序列: {seen}"
    )


# ── 1b. 跨站点/跨端点/跨原语：全都共用同一个 IP，失败也只买一个替补 ─────────────
#
# Jason 2026-07-10 提的场景：「我要的不仅是东财的行情数据，还要某个个股数据，
# 还有其他网站的别的数据的时候，共享一个 IP 去获取吗？」
# 答案：是，而且这是**默认行为**——单例 + get_proxy() 复用，与站点无关。
# stale 守卫也作用在 IP 层而非站点层，所以失败时同样只买一个替补。

def test_cross_site_requests_share_one_ip_and_one_replacement(monkeypatch, pm):
    """东财行情 + 东财 K 线 + 雪球 + 腾讯 + akshare 并发取数：

    - 全程只用一个 IP（单例复用，与打哪个站无关）
    - 这个 IP 死了，5 条链路只买 1 个替补（stale 守卫，与站点无关）
    """
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    dead = "10.0.0.1"
    per_site_ips: dict[str, list[str]] = {}
    lock = threading.Lock()
    # 5 条链路都拿到 IP1 之后才让它们一起失败。否则起步晚的线程会直接拿到替补，
    # 根本没碰过 IP1 —— 那也是对的（单例共享），但测不到「撞死同一个 IP」这件事。
    all_hold_ip1 = threading.Barrier(5, timeout=5)

    def _record(site, ip):
        with lock:
            first = site not in per_site_ips
            per_site_ips.setdefault(site, []).append(ip)
        if first:
            all_hold_ip1.wait()

    class _Sess:
        def __init__(self, proxies):
            self.proxies = proxies

        def _go(self, url):
            ip = _ip_of(self.proxies)
            _record(url.split("/")[2], ip)      # 按域名记账
            if ip == dead:
                raise RuntimeError("Connection aborted")

        def get(self, url, **kw):
            self._go(url)

            class _R:
                status_code = 200

                def raise_for_status(self):
                    pass
            return _R()

        def request(self, method, url, **kw):   # bounded_get 走这条
            self._go(url)

            class _R:
                status_code = 200
                headers = {}

                def iter_content(self, chunk_size=65536):
                    yield b'{"ok":1}'

                def close(self):
                    pass
            return _R()

    monkeypatch.setattr(dom, "make_domestic_session", lambda proxies=None: _Sess(proxies))

    # akshare 那条路不走 session，靠 proxy_env 注入；用当前 IP 判活死
    def _fake_ak():
        ip = pm.current_proxy.ip if pm.current_proxy else "direct"
        _record("akshare", ip)
        if ip == dead:
            raise RuntimeError("akshare 被掐断")
        return "df"
    _fake_ak.__name__ = "stock_individual_info_em"

    jobs = [
        lambda: dom.domestic_get("https://push2.eastmoney.com/api/qt/ulist.np/get", max_rounds=4),
        lambda: dom.domestic_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", max_rounds=4),
        lambda: dom.domestic_get("https://stock.xueqiu.com/v5/stock/quote.json", max_rounds=4),
        lambda: dom.domestic_bounded_get("https://qt.gtimg.cn/q=sh600519", max_rounds=4),
        lambda: dom.domestic_akshare(_fake_ak, max_rounds=4),
    ]

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = [f.result() for f in [pool.submit(j) for j in jobs]]

    assert all(r is not None for r in results), "5 条链路都该最终成功"
    assert len(per_site_ips) == 5, f"应有 5 个不同来源，实际 {sorted(per_site_ips)}"

    # ① 共享：barrier 保证 5 条链路第一次出网时拿到的是同一个 IP（单例复用，与站点无关）
    assert all(ips[0] == dead for ips in per_site_ips.values()), \
        f"5 条链路第一次出网该复用同一个 IP: {per_site_ips}"

    # ② 收敛：同一个 IP 死了之后，5 条链路落到同一个替补上（stale 守卫，与站点无关）
    replacements = {ips[-1] for ips in per_site_ips.values()}
    assert len(replacements) == 1, f"5 条链路落到了 {len(replacements)} 个不同替补: {per_site_ips}"

    # ③ 账单：全程只买 2 个 IP。没有守卫的话是 6 个（IP1 + 每条链路各买一个）
    assert pm.api.calls == 2, (
        f"跨 5 个站点/端点，只该买 2 个 IP（IP1 + 一个替补），实际买了 {pm.api.calls}。"
        f"\n各来源用过的 IP: {per_site_ips}"
    )


# ── 2. domestic_rotate：一批共享一个 IP，整批失败才换 ──────────────────────────

def test_batch_shares_one_ip_and_rotates_as_a_whole(monkeypatch, pm):
    """5 个并发子请求共享同一个 proxies；整批失败换一个 IP 重来整批。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)

    rounds = []   # 每轮用到的 IP 集合

    def _run_batch(proxies):
        ip = _ip_of(proxies)
        rounds.append(ip)

        def _one(i):
            if ip == "10.0.0.1":
                raise RuntimeError(f"子请求 {i} 死在 {ip}")
            return f"ok-{i}"

        with ThreadPoolExecutor(max_workers=5) as pool:
            return [f.result() for f in [pool.submit(_one, i) for i in range(5)]]

    out = domestic_rotate(_run_batch, what="指数批量", max_rounds=3, proxy_mgr=pm)

    assert out == [f"ok-{i}" for i in range(5)]
    assert rounds == ["10.0.0.1", "10.0.0.2"], f"整批换 IP 重来，实际轮次: {rounds}"
    assert pm.api.calls == 2, f"一批只该占一个 IP，买了 {pm.api.calls} 个"


def test_batch_body_sees_none_proxies_when_no_proxy_configured(monkeypatch):
    """未配快代理 → run(None)，直连是唯一选项（不是降级）。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: None)
    got = []
    assert domestic_rotate(lambda p: got.append(p) or "done", what="x") == "done"
    assert got == [None]


def test_batch_inherits_the_iron_rule(monkeypatch, pm):
    """配了快代理却取不到 IP → 一个子请求都不许发。"""
    import net.domestic as dom

    monkeypatch.setattr(dom, "get_proxy_manager", lambda: pm)
    monkeypatch.setattr(pm, "_fetch_one_proxy_locked", lambda: None)

    ran = []
    with pytest.raises(ProxyExhaustedError):
        domestic_rotate(lambda p: ran.append(1), what="指数批量", proxy_mgr=pm)
    assert ran == [], "额度耗尽时跑了批体 —— 那就是降级直连"


# ── 3. switch_proxy(stale=...) 守卫本体 ───────────────────────────────────────

def test_switch_is_free_when_someone_else_already_switched(pm):
    """别人已经把我手上那个换掉了 → 白捡新 IP，不扣额度。"""
    ip1 = pm.get_proxy()
    assert ip1.ip == "10.0.0.1" and pm.api.calls == 1

    ip2 = pm.switch_proxy(stale=ip1)             # 第一个换的人：买
    assert ip2.ip == "10.0.0.2" and pm.api.calls == 2

    late = pm.switch_proxy(stale=ip1)            # 拿着旧 IP 来的人：白捡
    assert late is ip2, "该复用别人换来的 IP"
    assert pm.api.calls == 2, "别人换过了还去买，就是超买"


def test_switch_with_the_current_ip_does_buy(pm):
    """手上就是最新的 IP 且它死了 → 必须真买一个新的。"""
    ip1 = pm.get_proxy()
    ip2 = pm.switch_proxy(stale=ip1)
    ip3 = pm.switch_proxy(stale=ip2)             # ip2 是最新的，得买
    assert ip3.ip == "10.0.0.3" and pm.api.calls == 3


def test_switch_buys_when_replacement_already_expired(pm, monkeypatch):
    """别人换来的新 IP 已经过期 → 不能白捡，得买。"""
    ip1 = pm.get_proxy()
    ip2 = pm.switch_proxy(stale=ip1)
    monkeypatch.setattr(type(ip2), "is_expired", property(lambda self: True))
    ip3 = pm.switch_proxy(stale=ip1)
    assert ip3.ip == "10.0.0.3" and pm.api.calls == 3


def test_get_proxy_reuses_unexpired_ip(pm):
    """没过期就复用，不扣额度。"""
    assert pm.get_proxy() is pm.get_proxy()
    assert pm.api.calls == 1


def test_switch_proxy_without_stale_is_still_unconditional(pm):
    """旧 API 语义不变：不传 stale 就是无条件买（尚未收编的调用方还在用它）。"""
    pm.get_proxy()
    pm.switch_proxy()
    pm.switch_proxy()
    assert pm.api.calls == 3
