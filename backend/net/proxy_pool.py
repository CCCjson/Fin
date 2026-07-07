"""
多槽代理池 — 支持 N 个并发出租的快代理 IP，每槽自带独立自适应限速器。

设计要点：
- ProxyManager 非线程安全（mutates current_proxy/fetch_count），所有取 IP
  调用都在池级锁内串行；取一个 IP 约 100-300ms，相对 5 分钟 IP 寿命可忽略。
- 懒加载：构造时不预取 IP，首次 acquire 到某槽时才向快代理要，避免慢路径
  为空时白烧配额。
- 快代理未配置（.env 无 kuaidaili_api）时退化为 size=1 直连槽 + 保守延迟，
  与现有无代理行为一致；有配置但取 IP 失败的槽也降级为保守直连节奏，
  绝不让多个槽并发裸打数据源。
"""
import queue
import random
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from loguru import logger

from net.proxy_manager import ProxyManager, ProxyInfo

# 直连（无代理）时的保守限速区间
DIRECT_MIN_DELAY = 2.0
DIRECT_MAX_DELAY = 8.0


class RateLimiter:
    """自适应限速器（与 scripts/eastmoney_crawler.py 同款实现，
    在 net/ 内自持一份避免反向依赖 scripts/）"""

    def __init__(self, min_delay: float, max_delay: float):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.current_delay = min_delay
        self.last_request_time = 0.0
        self.consecutive_success = 0
        self.consecutive_fail = 0

    def wait(self) -> None:
        """等待适当时间"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.current_delay:
            sleep_time = self.current_delay - elapsed
            jitter = random.uniform(-0.3, 0.5)
            sleep_time = max(0.1, sleep_time + jitter)
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def on_success(self) -> None:
        self.consecutive_success += 1
        self.consecutive_fail = 0
        if self.consecutive_success >= 10:
            self.current_delay = max(self.min_delay, self.current_delay * 0.9)
            self.consecutive_success = 0

    def on_failure(self, is_rate_limit: bool = False) -> None:
        self.consecutive_fail += 1
        self.consecutive_success = 0
        if is_rate_limit:
            self.current_delay = min(self.max_delay * 2, self.current_delay * 2)
        else:
            self.current_delay = min(self.max_delay, self.current_delay * 1.2)

    def reset(self) -> None:
        self.current_delay = self.min_delay
        self.consecutive_success = 0
        self.consecutive_fail = 0

    def set_delays(self, min_delay: float, max_delay: float) -> None:
        """调整限速区间（槽降级直连时收紧节奏用）"""
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.current_delay = max(self.current_delay, min_delay)


class ProxySlot:
    """一个可独占出租的代理槽：IP + 独立限速器 + 失败状态"""

    def __init__(self, slot_id: int, min_delay: float, max_delay: float):
        self.slot_id = slot_id
        self.proxy: Optional[ProxyInfo] = None
        self.rate_limiter = RateLimiter(min_delay, max_delay)
        self.consecutive_failures = 0
        self.failed = False  # report_failure 标记，下次 acquire 时换 IP

    @property
    def is_direct(self) -> bool:
        return self.proxy is None

    def to_requests_proxies(self) -> Optional[Dict[str, str]]:
        """转为 requests 的 proxies 参数；直连槽返回 None"""
        return self.proxy.to_requests_proxies() if self.proxy else None


class ProxyPool:
    """N 槽代理池，槽位独占出租，IP 过期/失效自动换新"""

    def __init__(
        self,
        size: int = 4,
        mgr: Optional[ProxyManager] = None,
        min_delay: float = 0.3,
        max_delay: float = 1.5,
    ):
        self.mgr = mgr or ProxyManager()
        self._mgr_lock = threading.Lock()
        self.direct_mode = not bool(self.mgr.api_url)

        if self.direct_mode:
            logger.warning("未配置 kuaidaili_api，代理池退化为单槽直连模式")
            size = 1
            min_delay, max_delay = DIRECT_MIN_DELAY, DIRECT_MAX_DELAY

        self.size = size
        self._proxy_min_delay = min_delay
        self._proxy_max_delay = max_delay
        self.ip_fetches = 0
        self.failures = 0

        self._queue: "queue.Queue[ProxySlot]" = queue.Queue()
        for i in range(size):
            self._queue.put(ProxySlot(i, min_delay, max_delay))

    def acquire(self, timeout: float = 30) -> ProxySlot:
        """取一个槽（阻塞直到有空槽）；确保槽内 IP 可用或已降级直连"""
        slot = self._queue.get(timeout=timeout)
        if not self.direct_mode and (
            slot.proxy is None or slot.failed or slot.proxy.is_expired
        ):
            self._refresh_slot(slot)
        return slot

    def release(self, slot: ProxySlot) -> None:
        """归还槽位"""
        self._queue.put(slot)

    def report_failure(self, slot: ProxySlot) -> None:
        """标记槽内 IP 失效，下次 acquire 时换新"""
        slot.failed = True
        slot.consecutive_failures += 1
        self.failures += 1

    @contextmanager
    def lease(self, timeout: float = 30) -> Iterator[ProxySlot]:
        slot = self.acquire(timeout=timeout)
        try:
            yield slot
        finally:
            self.release(slot)

    def refresh(self, slot: ProxySlot) -> ProxySlot:
        """立即为已持有的槽换新 IP（持槽模式下 worker 遇 IP 过期/失效时调用）"""
        self._refresh_slot(slot)
        return slot

    def _refresh_slot(self, slot: ProxySlot) -> None:
        with self._mgr_lock:
            proxy = self.mgr.fetch_one_proxy()
        slot.failed = False
        if proxy:
            self.ip_fetches += 1
            slot.proxy = proxy
            slot.consecutive_failures = 0
            slot.rate_limiter.set_delays(self._proxy_min_delay, self._proxy_max_delay)
            slot.rate_limiter.reset()
        else:
            # 取不到 IP → 该槽降级直连并收紧节奏，避免并发裸打数据源
            slot.proxy = None
            slot.rate_limiter.set_delays(DIRECT_MIN_DELAY, DIRECT_MAX_DELAY)
            logger.warning(f"代理槽 {slot.slot_id} 未取到 IP，降级为直连保守节奏")

    @property
    def stats(self) -> Dict:
        return {
            "size": self.size,
            "direct_mode": self.direct_mode,
            "ip_fetches": self.ip_fetches,
            "failures": self.failures,
        }

    def close(self) -> None:
        """预留接口：当前无持久资源需要释放"""
        return None
