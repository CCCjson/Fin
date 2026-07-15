"""
read_url — URL → 干净正文。

移植自 Scrapper missing_data/downloader.py 的 `_extension_for` / `_download_one` +
PDF 走 PyMuPDF；**HTML 升级用 trafilatura 去噪（去导航/页脚），失败回退 bs4 去标签**。

plain requests 下载失败（强反爬 403/JS 墙）时，回退 curl_cffi 重试一次。
"""
import os
from urllib.parse import urlparse

from loguru import logger

from acquisition.websearch.session import (
    make_plain_session, make_cffi_session, USER_AGENT, DOWNLOAD_TIMEOUT,
    bounded_get, Capped,
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


def _cleanup(cap: "Capped | None") -> None:
    """删掉落盘的临时 PDF 文件。"""
    if cap is not None and cap.path:
        try:
            os.unlink(cap.path)
        except OSError:
            pass


def _download(url: str, session) -> "Capped | None":
    """稳健 GET（有 50MB 上限、PDF 自动切块落盘）。返回 Capped 或 None。

    Capped.path 有值 → PDF 落盘（内存恒定）；Capped.data 有值 → HTML/其它在内存。
    """
    try:
        cap = bounded_get(url=url, session=session, mode="auto",
                          headers=HEADERS, timeout=DOWNLOAD_TIMEOUT)
    except Exception:
        return None
    if cap.status != 200:
        _cleanup(cap)
        return None
    if cap.too_large:
        logger.warning(f"read_url 响应过大跳过（>上限）：{url} [{cap.content_type}]")
        return None
    if cap.path is not None:
        try:
            if os.path.getsize(cap.path) < 200:
                _cleanup(cap)
                return None
        except OSError:
            _cleanup(cap)
            return None
    elif not cap.data or len(cap.data) < 200:
        return None
    return cap


def _extract_pdf(path: str, pages: int) -> tuple[str, str]:
    """从落盘的 PDF 文件解析（fitz.open filename，内存比 stream=bytes 更省）。"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(filename=path)
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

    try:
        if got.path is not None:                     # PDF：从落盘文件解析
            text, title = _extract_pdf(got.path, pdf_pages)
            title = title or _domain(url)
        else:                                        # HTML/其它：内存 bytes
            text, title = _extract_html(got.data, url)
    finally:
        _cleanup(got)

    return {
        "title": title, "url": url, "snippet": "",
        "text": (text or "")[:max_chars], "source": f"url:{_domain(url)}",
    }


def fetch_pdf_text(
    url: str,
    *,
    max_pages: int = 20,
    headers: dict | None = None,
    timeout: int = 30,
) -> str:
    """curl_cffi(chrome120 指纹) 强制直连抓 PDF 直链，抽前 max_pages 页正文。

    给「已探明无盾、直连即取」的国内财务 PDF 直链用（cninfo static 法定披露 /
    dfcfw 研报全文）。force_direct=True：这些是国内站，既不走海外代理也不走快代理池，
    显式空串直连（curl_cffi 的 trust_env 不够可靠，见 session.make_cffi_session）。
    切块落盘（内存恒定）+ 校验 %PDF 头，抓取/解析失败一律返回 ""，调用方按空判失败。

    Args:
        url: PDF 直链。空串直接返回 ""。
        max_pages: 抽前 N 页（法定披露年报几百页，取精华避免库爆）。
        headers: 附加请求头（如 dfcfw 需 referer；curl_cffi impersonate 已带浏览器头，
            别在此覆盖 UA）。
        timeout: 单请求超时秒。

    Returns:
        前 max_pages 页拼接的纯文本；抓取/解析失败或非 PDF 返回 ""。
    """
    if not url:
        return ""
    cap: Capped | None = None
    try:
        session = make_cffi_session(force_direct=True)
        cap = bounded_get(url=url, session=session, mode="file", file_suffix=".pdf",
                          headers=headers, timeout=timeout)
        if cap.status != 200 or not cap.path:
            return ""
        with open(cap.path, "rb") as f:
            if f.read(4) != b"%PDF":
                return ""
        text, _ = _extract_pdf(cap.path, max_pages)
        return text
    except Exception as e:  # noqa: BLE001
        logger.debug(f"fetch_pdf_text 抓取失败 {url}: {e}")
        return ""
    finally:
        _cleanup(cap)
