"""
东方财富 A股实时行情获取器

- 并发分页获取全市场 5000+ 只股票当日行情
- 自动代理切换（被限流后自动启用/切换代理）
- 支持大盘指数获取
"""

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    session: requests.Session,
    max_retries: int = 2,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    获取单页实时行情

    Args:
        session: 已配置好代理的 requests.Session（trust_env=False）

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

    for retry in range(max_retries + 1):
        try:
            resp = session.get(
                url,
                params=params,
                headers=_build_headers(),
                timeout=8,
            )

            if resp.status_code in (403, 429, 503):
                logger.warning(f"第 {page} 页被限流 (status={resp.status_code}, retry={retry})")
                time.sleep(0.5 * (retry + 1))
                continue

            data = resp.json()
            if data.get("rc") != 0:
                logger.warning(f"第 {page} 页 rc={data.get('rc')}, retry={retry}")
                time.sleep(0.3 * (retry + 1))
                continue

            raw_items = data.get("data", {}).get("diff", [])
            total = data.get("data", {}).get("total", 0)
            items = _parse_items(raw_items)
            return items, total

        except requests.RequestException as e:
            logger.warning(f"第 {page} 页请求异常 (retry={retry}): {type(e).__name__}")
            time.sleep(0.3 * (retry + 1))
            continue

    return [], 0


def fetch_a_share_realtime(
    proxies: Optional[Dict[str, str]] = None,
    sort_field: str = "f3",
    ascending: bool = False,
    progress_callback: Optional[object] = None,
) -> List[Dict[str, Any]]:
    """
    获取全部 A股实时行情（5000+ 只）

    策略：
    1. 先拉第 1 页获取 total 总数
    2. 计算剩余页数，并发拉取剩余页（max_workers=4）
    3. 合并、去重、返回

    Args:
        proxies: 代理配置（可选，被限流后自动切换代理）
        sort_field: 排序字段（f3=涨跌幅, f6=成交额, f8=换手率）
        ascending: 是否升序
        progress_callback: 进度回调 fn(fetched, total, page, total_pages)

    Returns:
        行情列表，每项包含 code, name, price, change_pct, volume 等
    """
    start_time = time.time()

    # ---- 获取 ProxyManager ----
    proxy_mgr = _get_proxy_manager()

    _first_proxy = [True]  # 用 list 绕过闭包限制

    def _new_session() -> requests.Session:
        """获取一个新快代理 IP 的 Session（绕过系统代理）"""
        if proxies:
            return _make_session(proxies)
        if proxy_mgr:
            # 第一次用 fetch_one_proxy，之后用 switch_proxy 强制换 IP
            if _first_proxy[0]:
                p = proxy_mgr.fetch_one_proxy()
                _first_proxy[0] = False
            else:
                p = proxy_mgr.switch_proxy()
            if p:
                logger.info(f"新代理: {p.ip}:{p.port}")
                return _make_session(p.to_requests_proxies())
        return _make_session()

    # ---- Step 1: 拉第 1 页，确定 total ----
    session = _new_session()
    page1_items, total = _fetch_one_page(1, sort_field, ascending, session, max_retries=3)

    if not page1_items:
        session = _new_session()
        page1_items, total = _fetch_one_page(1, sort_field, ascending, session, max_retries=3)

    if not page1_items:
        logger.error("实时行情第 1 页获取失败")
        return []

    items_per_page = len(page1_items)
    total_pages = min(math.ceil(total / items_per_page), 80) if items_per_page > 0 else 1
    logger.info(f"第 1 页: {items_per_page} 条, total={total}, 共 {total_pages} 页")

    if progress_callback:
        progress_callback(items_per_page, total, 1, total_pages)

    if total_pages <= 1:
        return page1_items

    # ---- Step 2: 串行拉取，每 10 页换一次快代理 IP ----
    all_data: List[Dict[str, Any]] = list(page1_items)
    failed_pages: List[int] = []
    pages_on_current_proxy = 1  # 第 1 页已用当前 session
    proxy_rotate_interval = 10

    for page in range(2, total_pages + 1):
        # 每 10 页换一个新代理
        if pages_on_current_proxy >= proxy_rotate_interval:
            session = _new_session()
            pages_on_current_proxy = 0

        items, _ = _fetch_one_page(page, sort_field, ascending, session, max_retries=2)
        if items:
            all_data.extend(items)
        else:
            failed_pages.append(page)

        pages_on_current_proxy += 1
        time.sleep(0.2)

        # 每页回调进度
        if progress_callback:
            progress_callback(len(all_data), total, page, total_pages)

        # 每 10 页打一次日志
        if page % 10 == 0:
            logger.info(f"进度: {len(all_data)}/{total} ({len(all_data)*100//total}%)")

    # ---- Step 3: 重试失败页 ----
    if failed_pages:
        logger.warning(f"{len(failed_pages)} 页失败，换代理重试...")
        retry_session = _new_session()
        still_failed = 0
        for p in sorted(failed_pages):
            items, _ = _fetch_one_page(p, sort_field, ascending, retry_session, max_retries=3)
            if items:
                all_data.extend(items)
            else:
                still_failed += 1
            time.sleep(0.3)
        if still_failed:
            logger.warning(f"最终仍有 {still_failed} 页失败")

    # ---- Step 4: 按 symbol 去重（取第一次出现的） ----
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
    获取大盘指数实时数据

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

    try:
        resp = requests.get(
            url,
            params=params,
            headers=_build_headers(),
            proxies=proxies,
            timeout=10,
        )
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
