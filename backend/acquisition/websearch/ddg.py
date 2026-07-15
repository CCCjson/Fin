"""
DuckDuckGo HTML 通用搜索 — 零密钥、无 JS、直连 html 端点。

移植自 Scrapper missing_data/searchers/ddg.py 的 `_unwrap_ddg_url` / `_parse_results` /
`_throttle` / `_http_search`，**绕过其矿业 QueryContext / _build_queries / search**，
只保留无状态的「query → 结果列表」原子能力。

限速纪律（DDG 软封很凶）：每次调用间隔 ≥BASE_GAP；202 软封退避 BACKOFF_ON_202 重试一次。
进程内单例维持节流状态连续（多引擎并发调时不各自重置 _last_call）。
"""
import os
import time
import threading
from urllib.parse import urlparse, parse_qs, unquote

from acquisition.websearch.session import (
    make_cffi_session, resolve_proxy, USER_AGENT, HTTP_TIMEOUT, bounded_get,
)

DDG_HTML = "https://html.duckduckgo.com/html/"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://duckduckgo.com/",
}


def _unwrap_ddg_url(href: str) -> str:
    """DDG 结果 URL 常被包成 //duckduckgo.com/l/?uddg=ENCODED_URL，解开它。"""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        return unquote(qs.get("uddg", [""])[0])
    return href


def _is_ad(href: str) -> bool:
    """DDG 广告链接（y.js 重定向 / bing aclick）——过滤掉。"""
    return ("duckduckgo.com/y.js" in href or "/y.js?ad_" in href
            or "bing.com/aclick" in href or "duckduckgo.com/y.js" in href)


def _parse_results(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for res in soup.select("div.result"):
        # 跳过广告结果（class 含 result--ad）
        cls = " ".join(res.get("class", []))
        if "result--ad" in cls or "result--ad-v2" in cls:
            continue
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = _unwrap_ddg_url(a.get("href", ""))
        if not href or _is_ad(href):
            continue
        title = a.get_text(" ", strip=True)
        sn = res.select_one(".result__snippet")
        snippet = sn.get_text(" ", strip=True) if sn else ""
        if title:
            out.append({"url": href, "title": title, "snippet": snippet})
    return out


class _DDG:
    """无状态搜索器（仅维持 session + 节流时间戳）。"""

    BACKOFF_ON_202 = 45.0

    def __init__(self):
        self.session = make_cffi_session()        # auto：Clash 在走 Clash，不在直连
        self._direct = None                       # 懒建的直连兜底 session
        self._last_call = 0.0
        self._lock = threading.Lock()

    def _direct_session(self):
        if self._direct is None:
            self._direct = make_cffi_session(force_direct=True)
        return self._direct

    def _one_get(self, session, q: str):
        """单次 GET（含 202 软封退避重试一次），返回 Capped。失败抛异常。"""
        cap = bounded_get(session, DDG_HTML, mode="bytes",
                          params={"q": q}, headers=HEADERS, timeout=HTTP_TIMEOUT)
        if cap.status == 202:
            time.sleep(self.BACKOFF_ON_202)
            cap = bounded_get(session, DDG_HTML, mode="bytes",
                              params={"q": q}, headers=HEADERS, timeout=HTTP_TIMEOUT)
        return cap

    @property
    def base_gap(self) -> float:
        try:
            return float(os.getenv("KNOWLEDGE_DDG_BASE_GAP", "8.0"))
        except ValueError:
            return 8.0

    def _throttle(self):
        now = time.time()
        gap = now - self._last_call
        if gap < self.base_gap:
            time.sleep(self.base_gap - gap)
        self._last_call = time.time()

    def http_search(self, q: str) -> list[dict]:
        with self._lock:                       # 节流串行（进程级）
            self._throttle()
            try:
                cap = self._one_get(self.session, q)
            except Exception:
                # 主通道连接失败（如 Clash 关了但 auto 选了代理，或代理 stale）→ 直连兜底重试一次
                if resolve_proxy() is not None:
                    try:
                        cap = self._one_get(self._direct_session(), q)
                    except Exception:
                        return []
                else:
                    return []
            if cap.status != 200:
                return []
            return _parse_results((cap.data or b"").decode("utf-8", "ignore"))


_singleton: _DDG | None = None
_singleton_lock = threading.Lock()


def _get() -> _DDG:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = _DDG()
    return _singleton


def web_search(query: str, max_results: int = 8, site: str | None = None) -> list[dict]:
    """
    DuckDuckGo 通用网页搜索。site 给定时拼 'site:xxx query'（给 arxiv/官网定向）。
    返回统一结构 [{title, url, snippet, text:'', source:'ddg_web'}]。
    """
    q = f"site:{site} {query}" if site else query
    raw = _get().http_search(q)
    out = []
    for r in raw[:max_results]:
        out.append({
            "title": r["title"], "url": r["url"], "snippet": r["snippet"],
            "text": "", "source": "ddg_web",
        })
    return out
