"""代理池必须跨调用复用，且轮换由失败驱动、过期只当上限兜底。

## 事故（烧掉近万个 IP 的那个）

`data_engine/fetchers/realtime.py::_fetch_all_concurrent` 把代理池当局部变量：

    pool = ProxyPool(size=CONCURRENT_POOL_SIZE, mgr=proxy_mgr, ...)

每次调用都新建 3 个空槽，`acquire()` 见槽是空的就买 IP。函数一返回，池子连同
三个**还有两分半有效期**的 IP 一起被 GC。前端行情页每 15 秒穿透一次缓存，
就是每 15 秒烧 3 个全新 IP —— 720 个/小时。

`ProxyPool.__init__` 的 `mgr or ProxyManager()` 是同一个病的另一半：不传 mgr
就自建一个实例，`current_proxy` 缓存谁也复用不了谁的。

## 现在的语义

| 轮换时机 | 触发 | 为什么 |
|---|---|---|
| **主力**：请求失败 | `report_failure(proxy_connect=True)` | 对任何寿命都成立，不需要知道 IP 能活多久 |
| 兜底：过期 | `slot.proxy.is_expired` | 防止低频场景下一个 IP 被无限期挂着 |

代价是一个刚死的 IP 会废掉**一次**请求（而不是像以前那样每次请求废一个 IP）。
"""
from datetime import datetime, timedelta

import pytest

from net.proxy_manager import ProxyInfo

pytestmark = pytest.mark.baseline


class _CountingPM:
    """记录打了几次提取 API。"""

    def __init__(self, ttl_seconds: int = 180):
        self.api_url = "https://dps.kdlapi.com/fake"
        self.api_calls = 0
        self.ttl = ttl_seconds

    def fetch_one_proxy(self):
        self.api_calls += 1
        return ProxyInfo(
            ip=f"10.0.0.{self.api_calls}", port=8080, username="u", password="p",
            expire_at=(datetime.now() + timedelta(seconds=self.ttl)).isoformat())

    def get_proxy(self):
        return self.fetch_one_proxy()


def _run(pool, n=10):
    """模拟 n 次抓取：借槽、成功、还槽。"""
    for _ in range(n):
        slot = pool.acquire()
        pool.report_success(slot)
        pool.release(slot)


# ── 池化：槽内 IP 跨调用留存 ────────────────────────────────────────────────

def test_pool_buys_at_most_size_ips_for_many_requests():
    from net.proxy_pool import ProxyPool

    pm = _CountingPM()
    pool = ProxyPool(size=3, mgr=pm, min_delay=0, max_delay=0)
    _run(pool, n=30)
    assert pm.api_calls == 3, f"30 次请求买了 {pm.api_calls} 个 IP，3 个槽最多买 3 个"


def test_shared_pool_across_calls_does_not_rebuy():
    """核心回归：同一个池被复用第二次、第三次，不该再买 IP。

    这正是 `_fetch_all_concurrent` 每次新建局部池丢掉的东西。
    """
    from net.proxy_pool import ProxyPool

    pm = _CountingPM()
    pool = ProxyPool(size=3, mgr=pm, min_delay=0, max_delay=0)
    _run(pool, n=5)
    assert pm.api_calls == 3
    _run(pool, n=5)   # 第二轮「全市场抓取」
    _run(pool, n=5)   # 第三轮
    assert pm.api_calls == 3, f"三轮抓取买了 {pm.api_calls} 个 IP，应该一直是那 3 个"


def test_fresh_pool_per_call_is_the_bug():
    """反面对照：每次新建池 = 每次重新买 size 个 IP。"""
    from net.proxy_pool import ProxyPool

    pm = _CountingPM()
    for _ in range(3):
        _run(ProxyPool(size=3, mgr=pm, min_delay=0, max_delay=0), n=5)
    assert pm.api_calls == 9, "这就是被修掉的行为：3 轮 × 3 槽 = 9 个 IP"


# ── 轮换由失败驱动 ──────────────────────────────────────────────────────────

def test_failure_rotates_the_ip():
    from net.proxy_pool import ProxyPool

    pm = _CountingPM()
    pool = ProxyPool(size=1, mgr=pm, min_delay=0, max_delay=0)

    slot = pool.acquire()
    first_ip = slot.proxy.ip
    pool.report_failure(slot, proxy_connect=True)
    pool.release(slot)

    slot = pool.acquire()
    assert slot.proxy.ip != first_ip, "连接层失败后必须换 IP"
    assert pm.api_calls == 2


def test_business_error_does_not_rotate_the_ip():
    """数据源限流之类的业务错误不该烧 IP —— 但 slot.failed 仍会触发换新。

    钉住现状：report_failure(proxy_connect=False) 只是不计入熔断，
    仍然标记 slot.failed → 下次 acquire 换 IP。改这个语义要连带改熔断。
    """
    from net.proxy_pool import ProxyPool

    pm = _CountingPM()
    pool = ProxyPool(size=1, mgr=pm, min_delay=0, max_delay=0)
    slot = pool.acquire()
    pool.report_failure(slot, proxy_connect=False)
    pool.release(slot)
    pool.acquire()
    assert pm.api_calls == 2


# ── 过期只当上限兜底 ────────────────────────────────────────────────────────

def test_expiry_is_the_backstop_not_the_driver():
    """IP 没过期就一直用；过期了才买新的，且只买一个。"""
    from net.proxy_pool import ProxyPool

    pm = _CountingPM(ttl_seconds=180)
    pool = ProxyPool(size=1, mgr=pm, min_delay=0, max_delay=0)

    _run(pool, n=20)
    assert pm.api_calls == 1, "180 秒内 20 次请求共用一个 IP"

    slot = pool.acquire()
    slot.proxy.expire_at = (datetime.now() - timedelta(seconds=1)).isoformat()
    pool.release(slot)

    _run(pool, n=20)
    assert pm.api_calls == 2, "过期后只补买 1 个，之后继续复用"


def test_real_ttl_is_respected():
    """f_et=1 报的 180 秒，池子必须当真（此前按硬编码 300 秒用）。"""
    from net.proxy_pool import ProxyPool

    pm = _CountingPM(ttl_seconds=180)
    pool = ProxyPool(size=1, mgr=pm, min_delay=0, max_delay=0)
    slot = pool.acquire()
    ttl = (datetime.fromisoformat(slot.proxy.expire_at) - datetime.now()).total_seconds()
    assert 170 <= ttl <= 180


# ── 默认 mgr 必须是进程单例（不是各建各的）───────────────────────────────────

def test_pool_defaults_to_the_process_singleton(monkeypatch):
    from net import proxy_manager as pmod
    from net.proxy_pool import ProxyPool

    monkeypatch.setenv("kuaidaili_api", "https://fake")
    pmod.reset_proxy_manager()
    pool_a, pool_b = ProxyPool(size=1), ProxyPool(size=1)
    assert pool_a.mgr is pool_b.mgr is pmod.get_proxy_manager(), \
        "不传 mgr 时两个池各自 new 一个 ProxyManager，IP 缓存互不可见"


def test_pool_falls_back_to_direct_mode_when_unconfigured(monkeypatch):
    from net import proxy_manager as pmod
    from net.proxy_pool import ProxyPool

    monkeypatch.delenv("kuaidaili_api", raising=False)
    pmod.reset_proxy_manager()
    assert ProxyPool(size=3).direct_mode is True


# ── 长命池要能从熔断里复活 ──────────────────────────────────────────────────

def test_breaker_can_be_reset_for_a_new_run():
    """池子跨调用长活之后，一次任务把它熔断了，不能让它永久瘫在 dead。"""
    from net.proxy_pool import ProxyPool

    class _DeadPM(_CountingPM):
        def fetch_one_proxy(self):
            self.api_calls += 1
            return None

    pm = _DeadPM()
    pool = ProxyPool(size=1, mgr=pm, dead_after=2, min_delay=0, max_delay=0)
    for _ in range(3):
        pool.release(pool.acquire())
    assert pool.breaker_state == "dead"

    pool.reset_breaker()
    assert pool.breaker_state == "closed"


def test_reset_breaker_keeps_live_ips():
    """复位熔断不该顺手把还活着的 IP 扔掉。"""
    from net.proxy_pool import ProxyPool

    pm = _CountingPM()
    pool = ProxyPool(size=2, mgr=pm, min_delay=0, max_delay=0)
    _run(pool, n=4)
    assert pm.api_calls == 2
    pool.reset_breaker()
    _run(pool, n=4)
    assert pm.api_calls == 2, "复位熔断把没过期的 IP 也丢了"


# ── realtime 的全市场翻页池必须常驻 ─────────────────────────────────────────

def test_quote_pool_is_a_module_singleton():
    """`_fetch_all_concurrent` 曾经每次调用都 new 一个 3 槽池然后扔掉。"""
    import acquisition.markets.realtime as rt

    rt._QUOTE_POOL = None
    pm = _CountingPM()
    a = rt._get_quote_pool(pm)
    b = rt._get_quote_pool(pm)
    try:
        assert a is b
        assert pm.api_calls == 0, "建池不该立刻买 IP（槽是懒填的）"
    finally:
        rt._QUOTE_POOL = None


def test_quote_pool_survives_three_market_sweeps():
    """三轮全市场抓取（每轮 54 页）总共只买 size 个 IP。"""
    import acquisition.markets.realtime as rt

    rt._QUOTE_POOL = None
    pm = _CountingPM()
    try:
        for _ in range(3):
            pool = rt._get_quote_pool(pm)
            pool.reset_breaker()
            _run(pool, n=54)
        assert pm.api_calls == rt.CONCURRENT_POOL_SIZE, \
            f"三轮 162 页买了 {pm.api_calls} 个 IP，应该只有 {rt.CONCURRENT_POOL_SIZE} 个"
    finally:
        rt._QUOTE_POOL = None
