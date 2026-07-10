"""
HTTP 会话层 — 隔离 curl_cffi 细节，给 DDG / read_url / EDGAR 复用。

移植自 Scrapper capitalIQ `create_session` 的「curl_cffi 指纹 + 可选代理」核心，
**只抄壳，不碰 token/2FA/playwright/真实密钥**。常量抄自 Scrapper config。

★ 代理兼容（Clash / 本地直连 自动适配）：默认不继承系统 HTTPS_PROXY（trust_env=False），
按 get_websearch_proxy() 策略显式决定走 Clash 还是直连。这样 Clash 关着、或将来本地能
直接翻墙（新加坡），websearch 都能用，无需改配置。
"""
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from knowledge_engine.config import get_scraper_proxy
from net.overseas import resolve_overseas   # 海外：先探直连,不通走 7898
from net import reset_probe

# 抄自 Scrapper config：chrome131 在该版 curl_cffi 有 TLS bug，统一用 chrome120 指纹
IMPERSONATE = "chrome120"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120


# ── 响应体积上限（防爬虫把整份大响应/大文件读进内存，撑爆 footprint）──
# 详见内存事故排查：无上限的 r.content / resp.json() 会让单次爬取瞬时冲到数 GB，
# Python 释放后 macOS malloc 也不还给 OS，footprint 棘轮式抬高。这里统一封顶。
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_RESPONSE_BYTES = _int_env("KNOWLEDGE_MAX_RESPONSE_MB", 50) * 1024 * 1024   # JSON/HTML 内存上限
MAX_FILE_BYTES = _int_env("KNOWLEDGE_MAX_FILE_MB", 200) * 1024 * 1024          # 大文件(PDF)落盘上限
_DL_CHUNK = 64 * 1024                                                          # 分块大小 64KB


def reset_proxy_probe() -> None:
    """清掉探测缓存（Clash 状态变化后想立即重判时调用）。"""
    reset_probe()


def resolve_proxy(force_direct: bool = False) -> str | None:
    """
    解析本次该用的代理 URL，None 表示直连。
    优先级：强反爬专用代理 KNOWLEDGE_SCRAPER_PROXY → 海外自适配 resolve_overseas
    （DDG/EDGAR 都是海外：先探本地直连,通就直连,不通走 Shadowrocket 7898）。
    """
    if force_direct:
        return None
    sp = get_scraper_proxy()                      # 强反爬专用代理优先
    if sp:
        return sp
    return resolve_overseas()                      # 海外：直连优先，不通走 7898


def make_cffi_session(force_direct: bool = False):
    """DDG / 强反爬源用：TLS 指纹伪装 + 按策略显式设代理。"""
    from curl_cffi import requests as cffi
    s = cffi.Session(impersonate=IMPERSONATE, trust_env=False)
    proxy = resolve_proxy(force_direct)
    # 显式设代理：有则走它，无则空串强制直连。
    # 注意：curl_cffi 的 trust_env=False 不够可靠（C 层仍会读 env 代理），
    # 必须显式空串才能 100% 覆盖系统 ALL_PROXY/HTTPS_PROXY。
    s.proxies = {"http": proxy, "https": proxy} if proxy else {"http": "", "https": ""}
    return s


def make_plain_session(force_direct: bool = False):
    """SEC EDGAR / 普通网页下载用：plain requests，按策略显式设代理。"""
    import requests
    s = requests.Session()
    s.trust_env = False                          # requests 这个足够禁用 env 代理
    proxy = resolve_proxy(force_direct)
    s.proxies = {"http": proxy, "https": proxy} if proxy else {"http": "", "https": ""}
    return s


# ────────────────────────────────────────────────────────────────────────
# 有上限的流式下载 —— requests 与 curl_cffi 通用（两者都支持 stream=True +
# iter_content + close）。所有爬虫响应入口统一走这里，杜绝无上限 r.content/json()。
# ────────────────────────────────────────────────────────────────────────
@dataclass
class Capped:
    """封顶下载结果。data（mode='bytes'）或 path（mode='file'）二选一有值。"""
    status: int = 0
    content_type: str = ""
    data: Optional[bytes] = None      # mode='bytes'
    path: Optional[str] = None        # mode='file'（临时文件，调用方用完删）
    truncated: bool = False           # 下载中途到上限被截断
    too_large: bool = False           # Content-Length 预判超限，直接没下载

    @property
    def usable_json(self) -> bool:
        """JSON 只有「完整下载」才可靠解析；截断/超限都视为不可用。"""
        return not (self.truncated or self.too_large)


def _content_length(resp) -> Optional[int]:
    try:
        v = resp.headers.get("content-length") or resp.headers.get("Content-Length")
        return int(v) if v is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _read_capped_bytes(resp, max_bytes: int) -> tuple[bytes, bool]:
    """流式读 body 累计到 max_bytes 就停。返回 (bytes, truncated)。"""
    total, chunks = 0, []
    for chunk in resp.iter_content(chunk_size=_DL_CHUNK):
        if not chunk:
            continue
        if total + len(chunk) > max_bytes:
            chunks.append(chunk[: max_bytes - total])
            return b"".join(chunks), True
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), False


def _stream_capped_to_file(resp, max_bytes: int, suffix: str) -> tuple[str, bool]:
    """流式把 body 分块写临时文件（内存恒定）。返回 (path, truncated)。调用方负责删文件。"""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="fin_dl_")
    total, truncated = 0, False
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=_DL_CHUNK):
                if not chunk:
                    continue
                if total + len(chunk) > max_bytes:
                    f.write(chunk[: max_bytes - total])
                    truncated = True
                    break
                f.write(chunk)
                total += len(chunk)
        return path, truncated
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def bounded_get(session, url: str, *, mode: str = "bytes", method: str = "GET",
                headers: Optional[dict] = None, params: Optional[dict] = None,
                timeout: int = DOWNLOAD_TIMEOUT, proxies: Optional[dict] = None,
                max_bytes: Optional[int] = None, file_suffix: str = ".bin",
                allow_redirects: bool = True) -> Capped:
    """
    统一「有上限」下载。requests / curl_cffi 的 Session 均可传入。

    mode='bytes' → Capped.data 为 bytes，超 MAX_RESPONSE_BYTES 截断（truncated=True）。
                   HTML/XML 可用截断结果；JSON 请查 .usable_json 再解析。
    mode='file'  → Capped.path 为临时文件路径，分块落盘，超 MAX_FILE_BYTES 截断。给
                   fitz.open(filename=path) 从磁盘解析，内存恒定。用完请 os.unlink。
    mode='auto'  → 按 content-type / URL 后缀判 PDF 走 file、其余走 bytes（read_url 用）。
    Content-Length 明确超限 → 直接短路不下载（too_large=True，data/path 均空）。
    """
    kw: dict = {"stream": True, "timeout": timeout, "allow_redirects": allow_redirects}
    if headers is not None:
        kw["headers"] = headers
    if params is not None:
        kw["params"] = params
    if proxies is not None:
        kw["proxies"] = proxies

    resp = session.request(method.upper(), url, **kw)
    try:
        status = getattr(resp, "status_code", 0) or 0
        ct = ""
        try:
            ct = resp.headers.get("content-type") or resp.headers.get("Content-Type") or ""
        except AttributeError:
            ct = ""
        # auto：按 content-type / URL 后缀决定 PDF 落盘 vs 其余进内存
        eff_mode = mode
        if mode == "auto":
            is_pdf = "pdf" in ct.lower() or url.split("?", 1)[0].lower().endswith(".pdf")
            eff_mode = "file" if is_pdf else "bytes"
        cap = max_bytes if max_bytes is not None else (
            MAX_FILE_BYTES if eff_mode == "file" else MAX_RESPONSE_BYTES)
        clen = _content_length(resp)
        if clen is not None and clen > cap:
            return Capped(status=status, content_type=ct, too_large=True)
        if eff_mode == "file":
            path, truncated = _stream_capped_to_file(resp, cap, file_suffix)
            return Capped(status=status, content_type=ct, path=path, truncated=truncated)
        data, truncated = _read_capped_bytes(resp, cap)
        return Capped(status=status, content_type=ct, data=data, truncated=truncated)
    finally:
        try:
            resp.close()
        except Exception:
            pass
