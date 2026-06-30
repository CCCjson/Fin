"""
read_url — URL → 干净正文。

移植自 Scrapper missing_data/downloader.py 的 `_extension_for` / `_download_one` +
PDF 走 PyMuPDF；**HTML 升级用 trafilatura 去噪（去导航/页脚），失败回退 bs4 去标签**。

plain requests 下载失败（强反爬 403/JS 墙）时，回退 curl_cffi 重试一次。
"""
import os
from urllib.parse import urlparse

from loguru import logger

from knowledge_engine.websearch.session import (
    make_plain_session, make_cffi_session, USER_AGENT, DOWNLOAD_TIMEOUT,
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/pdf,text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _extension_for(content_type: str, url: str) -> str:
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return ".pdf"
    if "html" in ct or "xml" in ct or "xhtml" in ct:
        return ".html"
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return ".pdf"
    if path.endswith((".htm", ".html")):
        return ".html"
    if path.endswith(".txt"):
        return ".txt"
    return ".bin"


def _download(url: str, session) -> tuple[bytes, str] | None:
    """稳健 GET，返回 (bytes, content_type) 或 None。"""
    try:
        r = session.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        content = r.content
    except Exception:
        return None
    if not content or len(content) < 200:
        return None
    return content, r.headers.get("Content-Type", "")


def _extract_pdf(blob: bytes, pages: int) -> tuple[str, str]:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=blob, filetype="pdf")
        n = min(pages, doc.page_count)
        text = "\n".join(doc[i].get_text() for i in range(n))
        title = (doc.metadata or {}).get("title", "") if doc.metadata else ""
        doc.close()
        return text, title
    except Exception:
        return "", ""


def _extract_html(blob: bytes, url: str) -> tuple[str, str]:
    """trafilatura 正文密度提取优先，失败回退 bs4 全文去标签。"""
    try:
        import trafilatura
        html = blob.decode("utf-8", errors="ignore")
        txt = trafilatura.extract(html, url=url, include_comments=False, favor_recall=True)
        if txt and txt.strip():
            title = ""
            try:
                meta = trafilatura.extract_metadata(html)
                title = getattr(meta, "title", "") or ""
            except Exception:
                pass
            return txt, (title or _domain(url))
    except Exception:
        pass
    # 回退：bs4 去标签
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(blob, "lxml")
        title = soup.title.get_text(strip=True) if soup.title else _domain(url)
        return soup.get_text(" ", strip=True), title
    except Exception:
        try:
            return blob.decode("utf-8", errors="ignore"), _domain(url)
        except Exception:
            return "", _domain(url)


def _default_max_chars() -> int:
    try:
        return int(os.getenv("KNOWLEDGE_WEBSEARCH_MAX_CHARS", "20000"))
    except ValueError:
        return 20000


def read_url(url: str, max_chars: int | None = None, pdf_pages: int = 8) -> dict:
    """
    抓 URL → 干净正文。返回统一结构 {title, url, snippet:'', text, source:'url:<domain>'}。
    抓取失败返回 text=''（调用方按空判失败）。
    """
    if max_chars is None:
        max_chars = _default_max_chars()

    # 通道兜底：plain(auto) → plain(直连，应对 Clash 关/代理 stale) → cffi(直连，应对强反爬)
    got = _download(url, make_plain_session())
    if got is None:
        try:
            got = _download(url, make_plain_session(force_direct=True))
        except Exception:
            got = None
    if got is None:
        try:
            got = _download(url, make_cffi_session(force_direct=True))
        except Exception:
            got = None
    if got is None:
        logger.warning(f"read_url 抓取失败: {url}")
        return {"title": "", "url": url, "snippet": "", "text": "", "source": f"url:{_domain(url)}"}

    blob, ct = got
    ext = _extension_for(ct, url)
    if ext == ".pdf":
        text, title = _extract_pdf(blob, pdf_pages)
        title = title or _domain(url)
    else:
        text, title = _extract_html(blob, url)

    return {
        "title": title, "url": url, "snippet": "",
        "text": (text or "")[:max_chars], "source": f"url:{_domain(url)}",
    }
