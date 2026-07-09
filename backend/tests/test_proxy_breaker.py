"""
ProxyPool 池级熔断器回归测试（2026-07-07 修订：去掉直连降级后的两态模型）。

背景：快代理 API 正常发 IP 但 IP 全连不上时，旧实现会让每个 worker 无限
换 IP 狂烧配额；上一版加的熔断又错误地「降级本地直连试探」，而东财高频
接口直连会被直接掐断（`ProtocolError('Connection aborted')`）、这类报错还
不被识别 → 卡在 open 到不了 dead → 进度冻结。

修订后熔断只有两态 closed→dead：请求失败一律换新快代理 IP 重试，连续换
`_dead_after` 个 IP 仍全部（连接层）失败 → dead，由消费方中止报错，绝不
降级直连。这里验证：
- 连续连接层失败达阈值 → dead，且此后不再买 IP（不烧配额）
- report_success 中途清零计数、避免累加到 dead
- 业务错误（限流，proxy_connect=False）不触发 dead
- is_proxy_connect_error 认得代理连不上/连接被掐/读超时，仍不认限流
- 无快代理配置（direct_mode）时熔断不介入
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from net.proxy_pool import ProxyPool, is_proxy_connect_error  # noqa: E402
from net.proxy_manager import ProxyInfo  # noqa: E402


class _FakeMgr:
    """假 ProxyManager：api_url 非空（走并发/非直连模式），fetch_one_proxy 受控。"""

    def __init__(self) -> None:
        self.api_url = "http://fake-kuaidaili.example/getdps"
        self.calls = 0
        self.next_proxy = None  # 测试按需设置返回值

    def fetch_one_proxy(self):
        self.calls += 1
        return self.next_proxy


def _make_proxy(ip: str = "1.2.3.4") -> ProxyInfo:
    return ProxyInfo(ip=ip, port=8080, expire_at=(datetime.now() + timedelta(minutes=5)).isoformat())


def test_is_proxy_connect_error_classification():
    # 代理本身连不上
    assert is_proxy_connect_error(
        Exception("连接异常: ProxyError('Unable to connect to proxy', RemoteDisconnected())")
    )
    assert is_proxy_connect_error(Exception("HTTPSConnectionPool(...): Connection refused"))
    assert is_proxy_connect_error(Exception("Tunnel connection failed: 403 Forbidden"))
    # 连接被对端掐断 / 读超时（额度尽误直连一次时东财掐直连的报错）
    assert is_proxy_connect_error(
        Exception("连接异常: ProtocolError('Connection aborted.', RemoteDisconnected())")
    )
    assert is_proxy_connect_error(Exception("HTTPSConnectionPool(...): Read timed out. (read timeout=8)"))
    # 限流 / 业务错误：不算连接层失败，不该计入熔断
    assert not is_proxy_connect_error(Exception("被限流 HTTP 429"))
    assert not is_proxy_connect_error(Exception("JSON 解析失败: Expecting value"))


def test_breaker_goes_dead_after_consecutive_failures_and_stops_buying_ip():
    mgr = _FakeMgr()
    mgr.next_proxy = _make_proxy()
    pool = ProxyPool(size=2, mgr=mgr, dead_after=4)

    slot = pool.acquire()  # 首次 acquire 触发一次 fetch_one_proxy
    assert mgr.calls == 1
    assert pool.breaker_state == "closed"

    # 连续 4 次连接层失败（每次都对应换了个新 IP 仍失败）→ dead
    for i in range(4):
        assert pool.breaker_state == "closed" if i < 3 else True
        pool.report_failure(slot, proxy_connect=True)
    assert pool.breaker_state == "dead"

    # dead 之后 refresh 不再买 IP（配额保护实锤）
    calls_before = mgr.calls
    pool.refresh(slot)
    assert mgr.calls == calls_before
    assert slot.is_direct  # 无代理，但消费方会据 breaker_state==dead 中止，不会真直连很久

    pool.release(slot)


def test_no_global_dead_after_success_floor_reached():
    """跑到中途才连不上（已成功一大批）时，不再全局中止——个别批次各自失败、
    任务继续。模拟：先成功跨过 success_floor，之后再多的连续失败也不 dead。"""
    mgr = _FakeMgr()
    mgr.next_proxy = _make_proxy()
    pool = ProxyPool(size=1, mgr=mgr, dead_after=3, success_floor=3)

    slot = pool.acquire()
    # 先成功 3 只，跨过 floor（证明快代理整体是通的）
    for _ in range(3):
        pool.report_success(slot)
    assert pool.stats["total_success"] == 3

    # 此后哪怕连续失败远超 dead_after，也不再全局 dead（局部批次连不上而已）
    for _ in range(20):
        pool.report_failure(slot, proxy_connect=True)
    assert pool.breaker_state == "closed"

    pool.release(slot)


def test_global_dead_only_before_success_floor():
    """开局就大面积连续失败（成功数还没跨过下限）→ 判定快代理整体挂了，dead。"""
    mgr = _FakeMgr()
    mgr.next_proxy = _make_proxy()
    pool = ProxyPool(size=1, mgr=mgr, dead_after=4, success_floor=100)

    slot = pool.acquire()
    for _ in range(4):
        pool.report_failure(slot, proxy_connect=True)
    assert pool.breaker_state == "dead"

    pool.release(slot)


def test_report_success_resets_and_prevents_dead():
    mgr = _FakeMgr()
    mgr.next_proxy = _make_proxy()
    pool = ProxyPool(size=1, mgr=mgr, dead_after=3)

    slot = pool.acquire()
    pool.report_failure(slot, proxy_connect=True)
    pool.report_failure(slot, proxy_connect=True)
    assert pool.stats["proxy_connect_failures"] == 2
    assert pool.breaker_state == "closed"

    # 一次成功就清零：证明只有「真·连续全失败」才会推进 dead
    pool.report_success(slot)
    assert pool.stats["proxy_connect_failures"] == 0

    # 再来 2 次失败仍不到阈值（3），不 dead
    pool.report_failure(slot, proxy_connect=True)
    pool.report_failure(slot, proxy_connect=True)
    assert pool.breaker_state == "closed"

    pool.release(slot)


def test_non_connect_failures_do_not_trip_breaker():
    mgr = _FakeMgr()
    mgr.next_proxy = _make_proxy()
    pool = ProxyPool(size=1, mgr=mgr, dead_after=2)

    slot = pool.acquire()
    for _ in range(10):
        pool.report_failure(slot, proxy_connect=False)  # 限流/业务错误，不该计入熔断
    assert pool.breaker_state == "closed"
    assert pool.stats["proxy_connect_failures"] == 0

    pool.release(slot)


def test_direct_mode_never_trips_breaker():
    """未配置 kuaidaili_api（api_url 为空）时永远直连，熔断逻辑不应介入。"""

    class _NoApiMgr:
        api_url = ""

        def fetch_one_proxy(self):
            raise AssertionError("direct_mode 下不应调用 fetch_one_proxy")

    pool = ProxyPool(size=4, mgr=_NoApiMgr(), dead_after=2)
    assert pool.direct_mode
    slot = pool.acquire()
    assert slot.is_direct

    for _ in range(10):
        pool.report_failure(slot, proxy_connect=True)
    assert pool.breaker_state == "closed"

    pool.release(slot)
