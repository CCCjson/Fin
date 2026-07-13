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
- 池级熔断器（2026-07-07 补）：快代理 API 正常发 IP 但 IP 连不上（区别于
  「取不到 IP」）时，旧逻辑会让每个 worker 无限换 IP 狂烧配额。熔断器在
  跨 worker 连续失败达阈值后停止买 IP、降级直连试探；直连也持续失败则
  判定彻底不可用（dead），交由消费方中止任务并给用户清晰报错。
"""
import queue
import random
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from loguru import logger

from net.proxy_manager import ProxyInfo, ProxyManager, get_proxy_manager

# 直连（无代理）时的保守限速区间
DIRECT_MIN_DELAY = 2.0
DIRECT_MAX_DELAY = 8.0

# 「连接层失败」的特征子串（区别于数据源限流/业务错误）；爬虫统一把各种
# 失败抛成同一个 ProxyTimeoutError，只能靠消息文本区分。既涵盖「代理本身
# 连不上」（ProxyError/tunnel failed），也涵盖「连接被对端掐断/读超时」
# （connection aborted/remote disconnected/read timed out）—— 后者是额度尽
# 误直连一次时东财掐直连的报错，也应计入「换 IP 计数」，否则永远收敛不到 dead。
_PROXY_CONNECT_ERROR_PATTERNS = (
    "unable to connect to proxy",
    "cannot connect to proxy",
    "proxyerror",
    "tunnel connection failed",
    "connection refused",
    "connection aborted",
    "remote disconnected",
    "read timed out",
)


def is_proxy_connect_error(exc: BaseException) -> bool:
    """判断异常是否属于「连接层失败」（代理连不上/连接被掐/读超时），
    而非数据源限流/业务错误。

    用于池级熔断计数：只有连接层失败才应计入「连续换 IP 全失败」阈值，
    避免正常的限流退避（HTTP 429/456）被误判成快代理服务不可用。
    """
    msg = str(exc).lower()
    return any(p in msg for p in _PROXY_CONNECT_ERROR_PATTERNS)


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
        self.proxy: ProxyInfo | None = None
        self.rate_limiter = RateLimiter(min_delay, max_delay)
        self.consecutive_failures = 0
        self.failed = False  # report_failure 标记，下次 acquire 时换 IP

    @property
    def is_direct(self) -> bool:
        return self.proxy is None

    def to_requests_proxies(self) -> dict[str, str] | None:
        """转为 requests 的 proxies 参数；直连槽返回 None"""
        return self.proxy.to_requests_proxies() if self.proxy else None


class ProxyPool:
    """N 槽代理池，槽位独占出租，IP 过期/失效自动换新"""

    def __init__(
        self,
        size: int = 4,
        mgr: ProxyManager | None = None,
        min_delay: float = 0.3,
        max_delay: float = 1.5,
        dead_after: int | None = None,
        success_floor: int = 200,
        initial_success: int = 0,
    ):
        # 默认走进程单例：`ProxyManager()` 每 new 一个就多一份彼此看不见的 IP
        # 缓存，两个池就是两份额度。未配快代理时单例是 None，退化成 direct_mode。
        self.mgr = mgr or get_proxy_manager() or ProxyManager()
        self._mgr_lock = threading.Lock()
        self.direct_mode = not bool(self.mgr.api_url)

        # 熔断只判「快代理服务从一开始就整体不可用」（额度尽/网络断），此时
        # 连续换 _dead_after 个新 IP 全失败即 dead、停止买 IP、消费方据
        # breaker_state == "dead" 中止本轮任务，避免对着一个挂掉的快代理白烧
        # 几万次配额。绝不降级本地直连（东财高频接口直连会被直接掐断，见
        # memory proxy-notes 文首铁律）。
        #
        # 关键：一旦已成功抓到 _success_floor 只（证明快代理整体是通的），
        # 熔断就永久失效——之后跑到中途某批股票（如一批冷门 ETF）连不上，
        # 只让它们各自重试到顶计单只失败、任务继续跑完剩下的，绝不因局部
        # 失败而全局中止（Jason 2026-07-07 拍板：ETF 重要性不高，宁可跑完）。
        #
        # initial_success：调用方可预置「本任务已在别处成功抓到的数量」。
        # 典型如 daily_updater：批量路径先抓了几千只（不走本 ProxyPool），
        # 慢路径才用本池；若不预置，慢路径开局恰好撞上一批连不上的 ETF 时，
        # 本池局部成功数还是 0，会误判「整体挂了」而中止——预置后本池一开始
        # 就知道快代理是通的，不再误伤。
        #
        # 阈值按调用方传入的原始并发规模算（direct_mode 下 size 被强制为 1，
        # 但那种情况本来就没有快代理、熔断不触发）。
        self._dead_after = dead_after if dead_after is not None else max(4, 2 * size)
        self._success_floor = success_floor
        self._state = "closed"  # closed | dead
        self._consec_proxy_failures = 0
        self._total_success = initial_success

        if self.direct_mode:
            logger.warning("未配置 kuaidaili_api，代理池退化为单槽直连模式")
            size = 1
            min_delay, max_delay = DIRECT_MIN_DELAY, DIRECT_MAX_DELAY

        self.size = size
        self._proxy_min_delay = min_delay
        self._proxy_max_delay = max_delay
        self.ip_fetches = 0
        self.failures = 0

        self._queue: queue.Queue[ProxySlot] = queue.Queue()
        for i in range(size):
            self._queue.put(ProxySlot(i, min_delay, max_delay))

    def acquire(self, timeout: float = 30) -> ProxySlot:
        """取一个槽（阻塞直到有空槽）。

        - `direct_mode`（未配快代理）：返回直连槽，合法。
        - 配了快代理：确保槽内有可用 IP；**取不到 IP 就抛 `ProxyExhaustedError`**，
          绝不交出一个 `proxy=None` 的槽让调用方拿去直连（铁律）。取不到 IP 已在
          `_refresh_slot` 里计入熔断计数，消费方据 `breaker_state=="dead"` 决定整体
          中止还是逐项失败。

        Raises:
            ProxyExhaustedError: 非 direct_mode 却取不到 IP（额度尽 / 已 dead）。
        """
        slot = self._queue.get(timeout=timeout)
        if not self.direct_mode and (
            slot.proxy is None or slot.failed or slot.proxy.is_expired
        ):
            self._refresh_slot(slot)
        if not self.direct_mode and slot.proxy is None:
            # 非直连模式却没 IP：归还槽再抛，绝不把会直连的空槽交出去。
            self.release(slot)
            from net.domestic import ProxyExhaustedError
            raise ProxyExhaustedError(
                f"代理槽 {slot.slot_id} 无可用快代理 IP，拒绝降级直连")
        return slot

    def release(self, slot: ProxySlot) -> None:
        """归还槽位"""
        self._queue.put(slot)

    def report_success(self, slot: ProxySlot) -> None:
        """请求成功后调用：清零池级「连续换 IP 全失败」计数（只要有一次成功，
        就说明快代理服务还活着，之前的连续失败不该累积推进 dead），并累计
        总成功数——一旦跨过 _success_floor，熔断永久失效（见 __init__ 注释）。"""
        slot.consecutive_failures = 0
        with self._mgr_lock:
            self._consec_proxy_failures = 0
            self._total_success += 1

    def report_failure(self, slot: ProxySlot, proxy_connect: bool = False) -> None:
        """标记槽内 IP 失效，下次 acquire 时换新。

        Args:
            proxy_connect: 是否属于「连接层失败」（用 `is_proxy_connect_error`
                判定：代理连不上/连接被掐/读超时）。只有这类失败才计入
                「连续换 IP 全失败」阈值；数据源限流等业务错误不触发熔断。
        """
        slot.failed = True
        slot.consecutive_failures += 1
        self.failures += 1

        # direct_mode（没配快代理）下没有 IP 可换，熔断无意义，不介入
        if not proxy_connect or self.direct_mode or self._state == "dead":
            return

        self._record_connect_failure()

    def _record_connect_failure(self) -> None:
        """记一次「连接层失败」进熔断计数，跨过阈值即判 dead。

        两个来源共用：请求命中连接层错误（`report_failure(proxy_connect=True)`）、
        或取新 IP 本身就失败（`_refresh_slot` 里 `fetch_one_proxy()` 返回 None）。
        后者以前只是警告一下就把槽位置空，指望之后一次注定失败的直连请求才能
        触发熔断计数——白白浪费一次直连探测，且东财偶尔没有立刻掐断时熔断会
        迟迟收敛不了。取不到 IP 本身就足以说明快代理这一环有问题，直接计数。
        """
        with self._mgr_lock:
            self._consec_proxy_failures += 1
            # 已成功抓到一大批后（快代理证明是通的），不再全局中止：此后个别
            # 股票/批次连不上只各自计单只失败，任务继续跑完剩下的。只有「开局
            # 就大面积连续失败、成功数还没跨过下限」才判定快代理整体挂了并中止。
            if (
                self._total_success < self._success_floor
                and self._consec_proxy_failures >= self._dead_after
            ):
                self._state = "dead"
                logger.error(
                    f"开局连续换 {self._consec_proxy_failures} 个快代理 IP 仍全部失败"
                    f"（累计仅成功 {self._total_success} 只），判定快代理服务当前"
                    f"不可用，中止本次任务（请检查快代理订单余额 / 网络）"
                )

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
            # dead：快代理服务已判定不可用，不再买 IP（消费方据 breaker_state
            # 中止）；否则正常换一个新 IP。绝不降级本地直连。
            proxy = None if self._state == "dead" else self.mgr.fetch_one_proxy()

        slot.failed = False
        if proxy:
            self.ip_fetches += 1
            slot.proxy = proxy
            slot.consecutive_failures = 0
            slot.rate_limiter.set_delays(self._proxy_min_delay, self._proxy_max_delay)
            slot.rate_limiter.reset()
        else:
            # 取不到 IP（额度尽/网络问题）或已 dead → slot 无代理。绝不降级
            # 直连试探，取不到 IP 本身就直接计入熔断计数（而不是留给下一次
            # 注定失败的直连请求去触发）。
            slot.proxy = None
            if self._state == "closed":
                logger.warning(f"代理槽 {slot.slot_id} 未取到新 IP（额度尽/网络问题？）")
                if not self.direct_mode:
                    self._record_connect_failure()

    def reset_breaker(self, initial_success: int = 0) -> None:
        """新一轮任务开始前复位熔断计数。

        池子跨调用长活（省 IP）之后就有了这个需求：某次任务把它熔断了，不能让它
        永久瘫在 dead —— 快代理可能只是当时抽风。槽内还没过期的 IP **不动**，
        复位的只是「这一轮连续失败了几次」。
        """
        with self._mgr_lock:
            self._state = "closed"
            self._consec_proxy_failures = 0
            self._total_success = initial_success

    @property
    def breaker_state(self) -> str:
        """熔断状态：closed=正常换 IP / dead=连续换 IP 全失败，快代理服务不可用"""
        return self._state

    @property
    def stats(self) -> dict:
        return {
            "size": self.size,
            "direct_mode": self.direct_mode,
            "ip_fetches": self.ip_fetches,
            "failures": self.failures,
            "breaker_state": self._state,
            "proxy_connect_failures": self._consec_proxy_failures,
            "total_success": self._total_success,
        }

    def close(self) -> None:
        """预留接口：当前无持久资源需要释放"""
        return None
