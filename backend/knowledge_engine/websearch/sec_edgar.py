"""
SEC EDGAR 全文检索 — 零密钥，仅需带邮箱的合规 User-Agent。

移植自 Scrapper missing_data/searchers/sec_edgar.py 的 `_hit_url` / `_hit_title` /
`_hit_doctype` / `_throttle` / `_http_search`，绕过其矿业 QueryContext / search。
UA 用 knowledge_engine config.get_edgar_ua()（EDGAR fair-use 要求带邮箱标识）。

EDGAR FTS 不返回正文 snippet → 命中即合成诚实 snippet（query 词保证在文中）；
要正文须把返回的 url 喂给 read_url。用 plain requests（EDGAR 不挑 TLS）。
"""
import time
import threading

from knowledge_engine.config import get_edgar_ua
from knowledge_engine.websearch.session import make_plain_session, resolve_proxy, HTTP_TIMEOUT

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_FMT = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/{file}"
TARGET_FORMS = "10-K,10-K/A,20-F,20-F/A,40-F,40-F/A,8-K,8-K/A,S-1,F-1"


def _headers() -> dict:
    return {"User-Agent": get_edgar_ua(), "Accept": "application/json"}


def _hit_url(hit: dict) -> str:
    src = hit.get("_source", {})
    ciks = src.get("ciks") or [""]
    cik = str(int(ciks[0])) if ciks[0] else ""        # 去前导零
    adsh = src.get("adsh", "").replace("-", "")
    _id = hit.get("_id", "")
    fname = _id.split(":")[-1] if ":" in _id else ""
    if not (cik and adsh and fname):
        return ""
    return ARCHIVE_FMT.format(cik=cik, adsh=adsh, file=fname)


def _hit_title(hit: dict) -> str:
    src = hit.get("_source", {})
    company = (src.get("display_names") or ["?"])[0]
    if "(CIK" in company:
        company = company.split("(CIK")[0].strip()
    form = src.get("form", "")
    ft = src.get("file_type", "")
    date = src.get("file_date", "")
    desc = src.get("file_description", "")
    if ft and ft != form:
        return f"{company} | {form} ({ft}: {desc}) | {date}"
    return f"{company} | {form} {desc} | {date}"


def _hit_doctype(hit: dict) -> str:
    src = hit.get("_source", {})
    parts = []
    for v in (src.get("file_type", ""), src.get("form", ""), src.get("file_description", "")):
        if v and v not in parts:
            parts.append(v)
    return " ".join(parts)


class _SEC:
    def __init__(self):
        self.session = make_plain_session()       # auto
        self._direct = None
        self._last_call = 0.0
        self._lock = threading.Lock()

    def _direct_session(self):
        if self._direct is None:
            self._direct = make_plain_session(force_direct=True)
        return self._direct

    def _throttle(self, min_gap: float = 0.25):
        now = time.time()
        gap = now - self._last_call
        if gap < min_gap:
            time.sleep(min_gap - gap)
        self._last_call = time.time()

    def _one_get(self, session, q: str, forms: str):
        return session.get(SEARCH_URL, params={"q": q, "forms": forms},
                           headers=_headers(), timeout=HTTP_TIMEOUT)

    def http_search(self, q: str, forms: str = TARGET_FORMS) -> list[dict]:
        with self._lock:
            self._throttle()
            try:
                r = self._one_get(self.session, q, forms)
            except Exception:
                if resolve_proxy() is not None:        # 主通道连接失败 → 直连兜底
                    try:
                        r = self._one_get(self._direct_session(), q, forms)
                    except Exception:
                        return []
                else:
                    return []
            if r.status_code != 200:
                return []
            try:
                data = r.json()
            except Exception:
                return []
            return data.get("hits", {}).get("hits", [])


_singleton: _SEC | None = None
_singleton_lock = threading.Lock()


def _get() -> _SEC:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = _SEC()
    return _singleton


def sec_search(query: str, max_results: int = 10, forms: str | None = None) -> list[dict]:
    """
    SEC EDGAR 全文检索（美股 10-K/20-F/8-K 等披露）。
    返回统一结构 [{title, url, snippet(合成), text:'', source:'sec_edgar'}]。
    """
    hits = _get().http_search(query, forms=forms or TARGET_FORMS)
    out = []
    seen = set()
    for h in hits:
        url = _hit_url(h)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({
            "title": _hit_title(h), "url": url,
            "snippet": f"SEC EDGAR 全文命中「{query}」（{_hit_doctype(h)}）",
            "text": "", "source": "sec_edgar",
        })
        if len(out) >= max_results:
            break
    return out
