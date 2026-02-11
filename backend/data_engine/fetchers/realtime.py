"""
东方财富 A股实时行情获取器

- 并发分页获取全市场 5000+ 只股票当日行情
- 自动代理切换（被限流后自动启用/切换代理）
- 支持大盘指数获取
"""

import math
import random
import threading
import time
from concurrent.futures import as_completed
from typing import Optional, Dict, List, Any, Tuple

import requests
from loguru import logger


# 东方财富实时行情字段映射
FIELD_MAP = {
    "f2": "price",          # 最新价
    "f3": "change_pct",     # 涨跌幅
    "f4": "change_amount",  # 涨跌额
    "f5": "volume",         # 成交量（手）
    "f6": "amount",         # 成交额
    "f7": "amplitude",      # 振幅
    "f8": "turnover",       # 换手率
    "f9": "pe_ratio",       # 市盈率
    "f12": "code",          # 股票代码
    "f13": "market_id",     # 市场ID (0=深圳, 1=上海)
    "f14": "name",          # 股票名称
    "f15": "high",          # 最高
    "f16": "low",           # 最低
    "f17": "open",          # 今开
    "f18": "prev_close",    # 昨收
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
    """延迟导入并创建 ProxyManager（避免循环依赖）"""
    try:
        import sys
        from pathlib import Path
        scripts_dir = str(Path(__file__).parent.parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from proxy_manager import ProxyManager
        return ProxyManager()
    except Exception as e:
        logger.debug(f"无法创建 ProxyManager: {e}")
        return None


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
    """
    创建绕过系统代理的 Session

    系统可能配置了 Clash/V2Ray 等本地代理 (127.0.0.1:7897)，
    并发请求时会压垮本地代理。用 trust_env=False 绕过，
    只使用显式传入的代理。
    """
    session = requests.Session()
    session.trust_env = False  # 忽略系统代理
    if proxies:
        session.proxies.update(proxies)
    return session


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


def fetch_a_share_realtime(
    proxies: Optional[Dict[str, str]] = None,
    sort_field: str = "f3",
    ascending: bool = False,
    progress_callback: Optional[object] = None,
) -> List[Dict[str, Any]]:
    """
    获取全部 A股实时行情（5000+ 只）

    策略（串行 + 快代理轮换，绕过 Clash TUN）：
    1. 通过快代理获取代理 IP
    2. 串行逐页获取，每 10 页切换新代理 IP
    3. 失败页收集后换代理重试
    4. 合并、去重、返回

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
                p = proxy_mgr.fetch_one_proxy()
                _first_proxy[0] = False
            else:
                p = proxy_mgr.switch_proxy()
            if p:
                logger.info(f"新代理: {p.ip}:{p.port}")
                return p.to_requests_proxies()
        return None

    # ---- 进度报告 ----
    _last_pct = [-1]

    def _report(fetched: int, total_count: int, pct: int):
        pct = max(0, min(pct, 100))
        if progress_callback and pct > _last_pct[0]:
            _last_pct[0] = pct
            progress_callback(fetched, total_count, pct, 100)

    # ---- Step 1: 拉第 1 页，确定 total ----
    cur_proxies = _get_fresh_proxy()
    page1_items, total = _fetch_one_page(1, sort_field, ascending, cur_proxies, max_retries=3)

    if not page1_items:
        # 换代理重试
        cur_proxies = _get_fresh_proxy()
        page1_items, total = _fetch_one_page(1, sort_field, ascending, cur_proxies, max_retries=3)

    if not page1_items:
        logger.error("实时行情第 1 页获取失败")
        return []

    items_per_page = len(page1_items)
    total_pages = min(math.ceil(total / items_per_page), 80) if items_per_page > 0 else 1
    logger.info(f"第 1 页: {items_per_page} 条, total={total}, 共 {total_pages} 页")

    _report(items_per_page, total, round(1 * 90 / total_pages))

    if total_pages <= 1:
        return page1_items

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
    seen = set()
    unique_data = []
    for row in all_data:
        sym = row.get("symbol", "")
        if sym and sym not in seen:
            seen.add(sym)
            unique_data.append(row)

    elapsed = time.time() - start_time
    logger.info(
        f"实时行情获取完成: {len(unique_data)} 条 "
        f"(原始 {len(all_data)}, 预期 {total}, "
        f"失败页 {len(failed_pages)}, 耗时 {elapsed:.1f}s)"
    )

    return unique_data


def fetch_index_realtime(
    proxies: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    获取大盘指数实时数据（通过快代理访问东财）

    Returns:
        指数列表（上证指数、深证成指、创业板指、科创50）
    """
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"

    # 上证指数=1.000001, 深证成指=0.399001, 创业板指=0.399006, 科创50=1.000688
    secids = "1.000001,0.399001,0.399006,1.000688"

    params = {
        "fltt": 2,
        "invt": 2,
        "fields": "f2,f3,f4,f6,f12,f14",
        "secids": secids,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "_": str(int(time.time() * 1000)),
    }

    # 如果没传代理，通过快代理获取（国内网站不走 Clash）
    if not proxies:
        proxy_mgr = _get_proxy_manager()
        if proxy_mgr:
            p = proxy_mgr.fetch_one_proxy()
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

        return result

    except requests.RequestException as e:
        logger.error(f"指数行情请求异常: {e}")
        return []
