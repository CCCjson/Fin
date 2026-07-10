"""
东方财富 A股实时行情获取器

- 并发分页获取全市场 5000+ 只股票当日行情（多代理 IP 并行，直连时回退串行）
- 自动代理切换（被限流后自动启用/切换代理）
- 短 TTL 单飞缓存（fetch_a_share_realtime_cached），多消费方共享一次拉取
- 腾讯/新浪批量接口兜底（东财失败或覆盖不足时自动补齐）
- 支持大盘指数获取
"""

import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

import requests
from loguru import logger

# 全市场覆盖低于该数量时触发腾讯/新浪备源补齐
FALLBACK_MIN_COVERAGE = 4500
# 并发翻页配置
CONCURRENT_POOL_SIZE = 3
CONCURRENT_PAGE_WORKERS = 6


# 东方财富实时行情字段映射
FIELD_MAP = {
    "f2": "price",          # 最新价
    "f3": "change_pct",     # 涨跌幅
    "f4": "change_amount",  # 涨跌额
    "f5": "volume",         # 成交量（手）
    "f6": "amount",         # 成交额
    "f7": "amplitude",      # 振幅
    "f8": "turnover",       # 换手率
    "f9": "pe_ratio",       # 市盈率（静态）
    "f12": "code",          # 股票代码
    "f13": "market_id",     # 市场ID (0=深圳, 1=上海)
    "f14": "name",          # 股票名称
    "f15": "high",          # 最高
    "f16": "low",           # 最低
    "f17": "open",          # 今开
    "f18": "prev_close",    # 昨收
    "f20": "total_mv",      # 总市值
    "f21": "circ_mv",       # 流通市值
    "f23": "pb_ratio",      # 市净率
    "f115": "pe_ttm",       # 市盈率(TTM)
}

FIELDS = ",".join(FIELD_MAP.keys())

# eastmoney API 实际每页上限（服务端强制限制，不受 pz 参数控制）
API_PAGE_SIZE = 100

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


def _build_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    }


def _get_proxy_manager():
    """延迟创建 ProxyManager（统一走 net 层）"""
    from net import get_proxy_manager
    return get_proxy_manager()


def _parse_items(raw_items: list) -> List[Dict[str, Any]]:
    """将 API 原始数据解析为标准化行情字典"""
    result = []
    for item in raw_items:
        row: Dict[str, Any] = {}
        for api_field, our_field in FIELD_MAP.items():
            val = item.get(api_field, "-")
            row[our_field] = val if val != "-" else None
        code = row.get("code", "")
        market_id = row.get("market_id")
        row["symbol"] = f"{code}.SH" if market_id == 1 else f"{code}.SZ"
        result.append(row)
    return result


def _make_session(proxies: Optional[Dict[str, str]] = None) -> requests.Session:
    """创建绕过系统代理的 Session（复用 net.make_domestic_session）。"""
    from net import make_domestic_session
    return make_domestic_session(proxies)


def _fetch_one_page(
    page: int,
    sort_field: str,
    ascending: bool,
    proxies: Optional[Dict[str, str]] = None,
    max_retries: int = 2,
    rate_limiter: Optional[threading.Semaphore] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    获取单页实时行情（每次调用自建独立 Session，线程安全）

    Args:
        proxies: 代理配置（传给 _make_session）
        rate_limiter: 限速信号量，控制并发请求速率

    Returns:
        (解析后的行情列表, 该页返回的 total 值)
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": str(page),
        "pz": str(API_PAGE_SIZE),
        "po": "0" if ascending else "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": sort_field,
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": FIELDS,
        "_": str(int(time.time() * 1000)),
    }

    # 限速：获取信号量后 sleep 一小段，错开并发请求
    if rate_limiter:
        rate_limiter.acquire()
        time.sleep(0.1)
        rate_limiter.release()

    session = _make_session(proxies)
    try:
        for retry in range(max_retries + 1):
            try:
                resp = session.get(
                    url,
                    params=params,
                    headers=_build_headers(),
                    timeout=3,
                )

                if resp.status_code in (403, 429, 503):
                    logger.warning(f"第 {page} 页被限流 (status={resp.status_code}, retry={retry})")
                    time.sleep(0.3 * (retry + 1))
                    continue

                data = resp.json()
                if data.get("rc") != 0:
                    logger.warning(f"第 {page} 页 rc={data.get('rc')}, retry={retry}")
                    time.sleep(0.2 * (retry + 1))
                    continue

                raw_items = data.get("data", {}).get("diff", [])
                total = data.get("data", {}).get("total", 0)
                items = _parse_items(raw_items)
                return items, total

            except requests.RequestException as e:
                logger.warning(f"第 {page} 页请求异常 (retry={retry}): {type(e).__name__}")
                time.sleep(0.2 * (retry + 1))
                continue
    finally:
        session.close()

    return [], 0


def _dedupe_by_symbol(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 symbol 去重，保留先出现的行"""
    seen = set()
    unique = []
    for row in rows:
        sym = row.get("symbol", "")
        if sym and sym not in seen:
            seen.add(sym)
            unique.append(row)
    return unique


def _maybe_fill_from_fallback(unique_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """东财覆盖不足时用腾讯/新浪备源补齐缺口（东财行优先）"""
    if len(unique_data) >= FALLBACK_MIN_COVERAGE:
        return unique_data
    try:
        from data_engine.fetchers.china_batch_quotes import fetch_a_share_realtime_via_fallback
        have = {r.get("symbol") for r in unique_data}
        extra = fetch_a_share_realtime_via_fallback(exclude=have)
        if extra:
            logger.info(f"备源补齐 {len(extra)} 只（东财 {len(unique_data)} 只覆盖不足）")
            unique_data = unique_data + extra
    except Exception as e:
        logger.warning(f"腾讯/新浪备源补齐失败: {e}")
    return unique_data


_QUOTE_POOL = None
_QUOTE_POOL_LOCK = threading.Lock()


def _get_quote_pool(proxy_mgr):
    """全市场翻页共用的常驻代理池。

    **绝不能每次调用都新建**：新池的槽是空的，`acquire()` 立刻买 3 个 IP，函数返回
    后连同还有两分半有效期的 IP 一起被 GC。前端行情页每 15 秒穿透一次缓存，就是
    每 15 秒烧 3 个全新 IP（720 个/小时）—— 这是烧掉近万个 IP 的那个 bug。

    常驻之后，槽内 IP 只在「请求失败」或「真的过期」时才换。挂机不抓取 = 零消耗。
    """
    global _QUOTE_POOL
    if _QUOTE_POOL is not None:
        return _QUOTE_POOL
    with _QUOTE_POOL_LOCK:
        if _QUOTE_POOL is None:
            from net.proxy_pool import ProxyPool
            _QUOTE_POOL = ProxyPool(size=CONCURRENT_POOL_SIZE, mgr=proxy_mgr,
                                    min_delay=0.15, max_delay=1.0)
    return _QUOTE_POOL


def _fetch_all_concurrent(
    sort_field: str,
    ascending: bool,
    proxy_mgr,
    report,
    start_time: float,
) -> List[Dict[str, Any]]:
    """并发翻页模式：N 个代理 IP 并行拉页，每 IP 独立限速。

    每 IP 请求节奏 ~0.15-1.0s（比旧串行版单 IP 连打 10 页还保守），
    3 IP 并行 → 54 页约 5-8s（旧串行 ~30s）。
    """
    pool = _get_quote_pool(proxy_mgr)
    pool.reset_breaker()   # 池子长活，别让上一轮的熔断计数误伤这一轮

    def _fetch_page_via_pool(page: int, max_attempts: int = 2,
                             retries: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        for _ in range(max_attempts):
            slot = pool.acquire()
            try:
                slot.rate_limiter.wait()
                items, total = _fetch_one_page(
                    page, sort_field, ascending,
                    slot.to_requests_proxies(), max_retries=retries,
                )
                if items:
                    slot.rate_limiter.on_success()
                    return items, total
                slot.rate_limiter.on_failure(is_rate_limit=True)
                pool.report_failure(slot)
            finally:
                pool.release(slot)
        return [], 0

    # ---- Step 1: 第 1 页确定 total ----
    page1_items, total = _fetch_page_via_pool(1, max_attempts=3, retries=1)
    if not page1_items:
        logger.error("实时行情第 1 页获取失败（并发模式），转备源")
        return _maybe_fill_from_fallback([])

    items_per_page = len(page1_items)
    total_pages = min(math.ceil(total / items_per_page), 80) if items_per_page > 0 else 1
    logger.info(f"第 1 页: {items_per_page} 条, total={total}, 共 {total_pages} 页（并发模式）")
    report(items_per_page, total, round(90 / total_pages))

    if total_pages <= 1:
        return _maybe_fill_from_fallback(page1_items)

    # ---- Step 2: 并发拉剩余页 ----
    all_data: List[Dict[str, Any]] = list(page1_items)
    failed_pages: List[int] = []
    lock = threading.Lock()
    done_count = [1]

    def _one_page(page: int) -> None:
        items, _ = _fetch_page_via_pool(page, max_attempts=2)
        with lock:
            done_count[0] += 1
            done = done_count[0]
            if items:
                all_data.extend(items)
            else:
                failed_pages.append(page)
            fetched = len(all_data)
        report(fetched, total, min(round(done * 95 / total_pages), 95))

    with ThreadPoolExecutor(max_workers=CONCURRENT_PAGE_WORKERS,
                            thread_name_prefix="rt-page") as ex:
        list(ex.map(_one_page, range(2, total_pages + 1)))

    # ---- Step 3: 失败页最后一轮重试 ----
    still_failed = 0
    if failed_pages:
        logger.warning(f"{len(failed_pages)} 页失败，最后一轮重试...")
        for i, page in enumerate(sorted(failed_pages)):
            items, _ = _fetch_page_via_pool(page, max_attempts=2, retries=1)
            if items:
                all_data.extend(items)
            else:
                still_failed += 1
            report(len(all_data), total, min(95 + round((i + 1) * 5 / len(failed_pages)), 100))
    else:
        report(len(all_data), total, 100)

    unique_data = _dedupe_by_symbol(all_data)
    elapsed = time.time() - start_time
    logger.info(
        f"实时行情获取完成(并发): {len(unique_data)} 条 "
        f"(原始 {len(all_data)}, 预期 {total}, 失败页 {still_failed}, "
        f"IP 使用 {pool.stats['ip_fetches']}, 耗时 {elapsed:.1f}s)"
    )
    return _maybe_fill_from_fallback(unique_data)


def fetch_a_share_realtime(
    proxies: Optional[Dict[str, str]] = None,
    sort_field: str = "f3",
    ascending: bool = False,
    progress_callback: Optional[object] = None,
) -> List[Dict[str, Any]]:
    """
    获取全部 A股实时行情（5000+ 只）

    策略：
    - 未显式传代理且配置了快代理 → 并发模式（多 IP 并行翻页，~5-8s）
    - 显式传代理或直连 → 串行模式（旧行为，逐页 + 轮换）
    - 覆盖不足/东财失败 → 腾讯/新浪备源自动补齐

    Args:
        proxies: 显式代理配置（可选，未传时自动通过快代理获取）
        sort_field: 排序字段（f3=涨跌幅, f6=成交额, f8=换手率）
        ascending: 是否升序
        progress_callback: 进度回调 fn(fetched, total, percent, 100)

    Returns:
        行情列表，每项包含 code, name, price, change_pct, volume 等
    """
    start_time = time.time()
    PROXY_ROTATE_INTERVAL = 10  # 每 10 页换一次代理

    # ---- 代理管理（始终使用快代理访问国内网站） ----
    proxy_mgr = _get_proxy_manager()
    _first_proxy = [True]

    def _get_fresh_proxy() -> Optional[Dict[str, str]]:
        """获取代理：优先用显式传入的，否则从快代理获取"""
        if proxies:
            return proxies
        if proxy_mgr:
            if _first_proxy[0]:
                # 复用进程内当前 IP（没过期就不扣额度）。直接 fetch_one_proxy()
                # 会让每次轮询都买一个新 IP —— 两个 30s 轮询器一天就是上千个。
                p = proxy_mgr.get_proxy()
                _first_proxy[0] = False
            else:
                p = proxy_mgr.switch_proxy()
            if p:
                logger.info(f"新代理: {p.ip}:{p.port}")
                return p.to_requests_proxies()
        return None

    # ---- 进度报告 ----
    _last_pct = [-1]
    _pct_lock = threading.Lock()

    def _report(fetched: int, total_count: int, pct: int):
        pct = max(0, min(pct, 100))
        with _pct_lock:
            if not (progress_callback and pct > _last_pct[0]):
                return
            _last_pct[0] = pct
        progress_callback(fetched, total_count, pct, 100)

    # ---- 并发模式：未显式传代理且快代理可用 ----
    if proxies is None and proxy_mgr is not None and getattr(proxy_mgr, "api_url", ""):
        return _fetch_all_concurrent(sort_field, ascending, proxy_mgr, _report, start_time)

    # ---- Step 1: 拉第 1 页，确定 total ----
    cur_proxies = _get_fresh_proxy()
    page1_items, total = _fetch_one_page(1, sort_field, ascending, cur_proxies, max_retries=3)

    if not page1_items:
        # 换代理重试
        cur_proxies = _get_fresh_proxy()
        page1_items, total = _fetch_one_page(1, sort_field, ascending, cur_proxies, max_retries=3)

    if not page1_items:
        logger.error("实时行情第 1 页获取失败，转备源")
        return _maybe_fill_from_fallback([])

    items_per_page = len(page1_items)
    total_pages = min(math.ceil(total / items_per_page), 80) if items_per_page > 0 else 1
    logger.info(f"第 1 页: {items_per_page} 条, total={total}, 共 {total_pages} 页")

    _report(items_per_page, total, round(1 * 90 / total_pages))

    if total_pages <= 1:
        return _maybe_fill_from_fallback(page1_items)

    # ---- Step 2: 串行逐页获取，失败立即换代理重试该页 ----
    all_data: List[Dict[str, Any]] = list(page1_items)
    failed_pages: List[int] = []
    pages_on_current_proxy = 1
    MAX_PAGE_ATTEMPTS = 3  # 每页最多换 3 次代理

    for page in range(2, total_pages + 1):
        # 定期轮换代理
        if pages_on_current_proxy >= PROXY_ROTATE_INTERVAL:
            cur_proxies = _get_fresh_proxy()
            pages_on_current_proxy = 0

        # 尝试获取该页，失败则换代理重试
        success = False
        for attempt in range(MAX_PAGE_ATTEMPTS):
            items, _ = _fetch_one_page(page, sort_field, ascending, cur_proxies, max_retries=0)
            if items:
                all_data.extend(items)
                pages_on_current_proxy += 1
                success = True
                break
            else:
                # 立即换代理重试
                cur_proxies = _get_fresh_proxy()
                pages_on_current_proxy = 0

        if not success:
            failed_pages.append(page)

        # 进度 0-95%
        pct = round(page * 95 / total_pages)
        _report(len(all_data), total, min(pct, 95))

        if page % 20 == 0:
            logger.info(f"进度: {len(all_data)}/{total} (页 {page}/{total_pages})")

        time.sleep(0.02)

    # ---- Step 3: 最终重试残余失败页（95%-100%） ----
    if failed_pages:
        logger.warning(f"{len(failed_pages)} 页最终失败，最后一轮重试...")
        cur_proxies = _get_fresh_proxy()
        still_failed = 0

        for i, page in enumerate(sorted(failed_pages)):
            if i > 0 and i % 3 == 0:
                cur_proxies = _get_fresh_proxy()

            items, _ = _fetch_one_page(page, sort_field, ascending, cur_proxies, max_retries=1)
            if items:
                all_data.extend(items)
            else:
                still_failed += 1

            pct = 95 + round((i + 1) * 5 / len(failed_pages))
            _report(len(all_data), total, min(pct, 100))

        if still_failed:
            logger.warning(f"最终仍有 {still_failed} 页失败")
    else:
        _report(len(all_data), total, 100)

    # ---- Step 4: 按 symbol 去重 ----
    unique_data = _dedupe_by_symbol(all_data)

    elapsed = time.time() - start_time
    logger.info(
        f"实时行情获取完成: {len(unique_data)} 条 "
        f"(原始 {len(all_data)}, 预期 {total}, "
        f"失败页 {len(failed_pages)}, 耗时 {elapsed:.1f}s)"
    )

    return _maybe_fill_from_fallback(unique_data)


# ======== 全市场行情短 TTL 单飞缓存 ========
# 多消费方（/realtime 路由、价格预警监控、日线批量路径）共享一次全量拉取，
# 避免 30s 轮询和页面刷新各自重复拉 5000+ 只。
_QUOTE_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_QUOTE_CACHE_LOCK = threading.Lock()
_QUOTE_FETCH_EVENT: Optional[threading.Event] = None


def fetch_a_share_realtime_cached(
    ttl: float = 20.0,
    force: bool = False,
    progress_callback: Optional[object] = None,
) -> List[Dict[str, Any]]:
    """带短 TTL 缓存的全市场实时行情（single-flight）。

    - 缓存新鲜（< ttl 秒）→ 直接返回缓存副本，进度回调立即打满
    - 缓存过期 → 第一个调用方真正拉取，其余并发调用方等待并复用结果
    - force=True → 发起方强制重新拉取（等待中的跟随方仍复用最新缓存）

    Returns:
        行情列表副本（浅拷贝，调用方可自行排序，勿改行内 dict）
    """
    global _QUOTE_FETCH_EVENT

    with _QUOTE_CACHE_LOCK:
        fresh = (
            _QUOTE_CACHE["data"] is not None
            and time.time() - _QUOTE_CACHE["ts"] < ttl
        )
        if fresh and not force:
            data = _QUOTE_CACHE["data"]
            if progress_callback:
                progress_callback(len(data), len(data), 100, 100)
            return list(data)

        if _QUOTE_FETCH_EVENT is None:
            _QUOTE_FETCH_EVENT = threading.Event()
            event = _QUOTE_FETCH_EVENT
            leader = True
        else:
            event = _QUOTE_FETCH_EVENT
            leader = False

    if leader:
        data: List[Dict[str, Any]] = []
        try:
            data = fetch_a_share_realtime(progress_callback=progress_callback)
            if data:
                with _QUOTE_CACHE_LOCK:
                    _QUOTE_CACHE["data"] = data
                    _QUOTE_CACHE["ts"] = time.time()
        finally:
            with _QUOTE_CACHE_LOCK:
                _QUOTE_FETCH_EVENT = None
            event.set()
        if data:
            return list(data)
        # 本次拉取失败 → 退回旧缓存（可能过期，聊胜于无）
        with _QUOTE_CACHE_LOCK:
            stale = _QUOTE_CACHE["data"]
        return list(stale) if stale else []

    # 跟随方：等发起方完成后复用缓存
    event.wait(timeout=120)
    with _QUOTE_CACHE_LOCK:
        data = _QUOTE_CACHE["data"]
    if progress_callback and data:
        progress_callback(len(data), len(data), 100, 100)
    return list(data) if data else []


def fetch_index_realtime(
    proxies: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    获取大盘指数实时数据（通过快代理访问东财）

    Returns:
        指数列表（上证指数、深证成指、创业板指、科创50）
    """
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"

    # 上证指数, 深证成指, 创业板指, 科创50, 恒生指数
    secids = "1.000001,0.399001,0.399006,1.000688,100.HSI"

    params = {
        "fltt": 2,
        "invt": 2,
        "fields": "f2,f3,f4,f6,f12,f14",
        "secids": secids,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "_": str(int(time.time() * 1000)),
    }

    # 如果没传代理，通过快代理获取（国内网站不走 Clash）。
    # get_proxy() 而非 fetch_one_proxy()：没过期就复用，别每次调用都买新 IP。
    if not proxies:
        proxy_mgr = _get_proxy_manager()
        if proxy_mgr:
            p = proxy_mgr.get_proxy()
            if p:
                proxies = p.to_requests_proxies()

    try:
        session = _make_session(proxies)
        try:
            resp = session.get(
                url,
                params=params,
                headers=_build_headers(),
                timeout=10,
            )
        finally:
            session.close()
        data = resp.json()

        if data.get("rc") != 0 or not data.get("data"):
            logger.warning(f"指数行情请求失败: rc={data.get('rc')}")
            return []

        result = []
        for item in data["data"].get("diff", []):
            result.append({
                "code": item.get("f12", ""),
                "name": item.get("f14", ""),
                "price": item.get("f2"),
                "change_pct": item.get("f3"),
                "change_amount": item.get("f4"),
                "amount": item.get("f6"),
            })

        # 补充纳斯达克（东财不支持，用 yfinance）
        try:
            nasdaq = _fetch_nasdaq()
            if nasdaq:
                result.append(nasdaq)
        except Exception as e:
            logger.warning(f"纳斯达克数据获取失败: {e}")

        return result

    except requests.RequestException as e:
        logger.error(f"指数行情请求异常: {e}")
        return []


def _symbol_to_secid(symbol: str) -> Optional[str]:
    """A股/指数 symbol → 东财 secid（市场.代码）。1=沪 0=深；非沪深返回 None。"""
    if "." not in symbol:
        return None
    code, suffix = symbol.split(".", 1)
    suffix = suffix.upper()
    if suffix == "SH":
        return f"1.{code}"
    if suffix == "SZ":
        return f"0.{code}"
    return None


def _looks_like_index(symbol: str) -> bool:
    """沪市 000xxx.SH / 深市 399xxx.SZ 视为指数（仅用于打 is_index 标记）。"""
    code = symbol[:6]
    return (symbol.endswith(".SH") and code.startswith("000")) or \
           (symbol.endswith(".SZ") and code.startswith("399"))


def fetch_quotes_by_symbols(symbols: List[str], max_rounds: int = 3) -> List[Dict[str, Any]]:
    """按 symbols 批量取 A股/指数实时行情（东财 ulist.np，走快代理 + 失败切 IP 重试）。

    secid 的「市场.代码」前缀天然区分沪深：个股与指数（含 000001.SH 上证指数
    vs 000001.SZ 平安银行）一次取回、无歧义。非沪深标的（HK/US）忽略，交由各自
    fetcher 处理。返回与 AShareFetcher.fetch_realtime 一致的 dict 形状。
    """
    secid_map: Dict[str, str] = {}  # secid -> 原始 symbol
    for s in symbols:
        sid = _symbol_to_secid(s)
        if sid:
            secid_map[sid] = s
    if not secid_map:
        return []

    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": 2,
        "invt": 2,
        "fields": "f2,f3,f4,f5,f6,f7,f8,f12,f13,f14,f15,f16,f17,f18",
        "secids": ",".join(secid_map.keys()),
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "_": str(int(time.time() * 1000)),
    }

    proxy_mgr = _get_proxy_manager()

    def _num(v, cast=float, default=None):
        if v is None or v == "-":
            return default
        try:
            return cast(v)
        except (TypeError, ValueError):
            return default

    for attempt in range(max_rounds):
        proxies = None
        if proxy_mgr:
            p = proxy_mgr.get_proxy() if attempt == 0 else proxy_mgr.switch_proxy()
            if p:
                proxies = p.to_requests_proxies()
                logger.info(f"实时行情使用快代理: {p.ip}:{p.port}（第 {attempt + 1} 轮）")

        session = _make_session(proxies)
        try:
            resp = session.get(url, params=params, headers=_build_headers(), timeout=8)
            data = resp.json()
            if data.get("rc") != 0 or not data.get("data"):
                logger.warning(f"批量行情 rc={data.get('rc')}（第 {attempt + 1} 轮），换 IP 重试")
                continue

            out: List[Dict[str, Any]] = []
            for it in data["data"].get("diff", []):
                code = it.get("f12", "")
                mid = it.get("f13")
                symbol = f"{code}.SH" if mid == 1 else f"{code}.SZ"
                out.append({
                    "symbol": symbol,
                    "name": it.get("f14", ""),
                    "price": _num(it.get("f2")),
                    "change": _num(it.get("f4"), float, 0),
                    "change_percent": _num(it.get("f3"), float, 0),
                    "volume": _num(it.get("f5"), int, 0),
                    "amount": _num(it.get("f6"), float, 0),
                    "open": _num(it.get("f17"), float, 0),
                    "high": _num(it.get("f15"), float, 0),
                    "low": _num(it.get("f16"), float, 0),
                    "prev_close": _num(it.get("f18")),
                    "amplitude": _num(it.get("f7")),
                    "turnover": _num(it.get("f8")),
                    "timestamp": datetime.now().isoformat(),
                    "is_index": _looks_like_index(symbol),
                })
            logger.info(f"批量实时行情成功获取 {len(out)}/{len(secid_map)} 只标的")
            return out

        except requests.RequestException as e:
            logger.warning(f"批量行情请求异常（第 {attempt + 1} 轮）: {type(e).__name__}，换 IP 重试")
            continue
        finally:
            session.close()

    logger.error(f"批量实时行情全部 {max_rounds} 轮均失败")
    return []


def _fetch_nasdaq() -> Optional[Dict[str, Any]]:
    """用 yfinance 获取纳斯达克综合指数"""
    import yfinance as yf
    ticker = yf.Ticker("^IXIC")
    hist = ticker.history(period="2d")
    if hist.empty or len(hist) < 1:
        return None
    latest = hist.iloc[-1]
    price = round(float(latest["Close"]), 2)
    if len(hist) >= 2:
        prev_close = float(hist.iloc[-2]["Close"])
        change_pct = round((price - prev_close) / prev_close * 100, 2)
        change_amount = round(price - prev_close, 2)
    else:
        change_pct = 0.0
        change_amount = 0.0
    return {
        "code": "IXIC",
        "name": "纳斯达克",
        "price": price,
        "change_pct": change_pct,
        "change_amount": change_amount,
        "amount": None,
    }


def compute_statistics(quotes: list) -> dict:
    """计算全市场涨跌统计（涨/跌/平/涨停/跌停家数）。

    涨跌停按**板块阈值**判定（主板 10% / 创业板·科创板 20%），真源 `common.limit_rules`。
    此前全市场一刀切 9.9%，把创业板/科创板 10%~20% 的普通上涨全记成了涨停。
    这三档阈值经实盘快照验证：主板非ST 0 越界、创业板 0 越界、科创板 1 只边界值。

    ⚠️ **刻意不传 `name`**，即不启用 ST 的 5% 阈值：本库里名字带 ST 的主板股有 40.5%
    （62/153）涨跌幅超过 ±5%，最深 -10.16%——`name` 与价格数据自相矛盾，靠名字判 ST
    不可靠。启用它会把跌停家数凭空抬高 95%。待名称来源查清后再开（见 docs/13）。

    没有 `symbol` 的行（如只带 change_pct 的测试桩）判不出板块，不计入涨跌停。
    """
    from common.limit_rules import is_limit_down, is_limit_up

    def _pct(q: dict):
        return q.get("change_pct")

    up = sum(1 for q in quotes if _pct(q) is not None and _pct(q) > 0)
    down = sum(1 for q in quotes if _pct(q) is not None and _pct(q) < 0)
    flat = sum(1 for q in quotes if _pct(q) is not None and _pct(q) == 0)
    limit_up = sum(1 for q in quotes if is_limit_up(q.get("symbol", ""), _pct(q)))
    limit_down = sum(1 for q in quotes if is_limit_down(q.get("symbol", ""), _pct(q)))
    return {
        "total": len(quotes),
        "up": up, "down": down, "flat": flat,
        "limit_up": limit_up, "limit_down": limit_down,
    }
