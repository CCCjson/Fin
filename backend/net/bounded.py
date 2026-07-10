"""
有上限的流式下载 —— 一切爬虫响应入口的统一门面。

## 为什么这个原语住在 net/ 而不是 acquisition/

它是**会话无关**的：只要求传入的 session 支持 `request(..., stream=True)` +
`iter_content()` + `close()`，requests / curl_cffi 都满足。既然 `net/domestic.py`
要用它把「换 IP 重试」和「响应封顶」合成同一条路径，它就必须待在 `net/` 或更下层
——`net/` 反向 import `acquisition/` 会把分层依赖倒过来。

## 它挡的是什么

无上限的 `r.content` / `resp.json()` 会让单次爬取瞬时冲到数 GB；Python 释放后
macOS malloc 也不把内存还给 OS，footprint 棘轮式抬高（实测后端涨到 22GB）。
这里统一封顶：内存流按 `MAX_RESPONSE_BYTES` 截断，大文件按 `MAX_FILE_BYTES`
分块落盘（内存恒定）。
"""
import os
import tempfile
from dataclasses import dataclass
from typing import Any


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_RESPONSE_BYTES = _int_env("KNOWLEDGE_MAX_RESPONSE_MB", 50) * 1024 * 1024   # JSON/HTML 内存上限
MAX_FILE_BYTES = _int_env("KNOWLEDGE_MAX_FILE_MB", 200) * 1024 * 1024          # 大文件(PDF)落盘上限
_DL_CHUNK = 64 * 1024                                                          # 分块大小 64KB


@dataclass
class Capped:
    """封顶下载结果。data（mode='bytes'）或 path（mode='file'）二选一有值。"""
    status: int = 0
    content_type: str = ""
    data: bytes | None = None      # mode='bytes'
    path: str | None = None        # mode='file'（临时文件，调用方用完删）
    truncated: bool = False           # 下载中途到上限被截断
    too_large: bool = False           # Content-Length 预判超限，直接没下载

    @property
    def usable_json(self) -> bool:
        """JSON 只有「完整下载」才可靠解析；截断/超限都视为不可用。"""
        return not (self.truncated or self.too_large)

    @property
    def ok(self) -> bool:
        """HTTP 成功且拿到了可用内容。"""
        return 200 <= self.status < 300 and not self.too_large


def _content_length(resp: Any) -> int | None:
    try:
        v = resp.headers.get("content-length") or resp.headers.get("Content-Length")
        return int(v) if v is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _read_capped_bytes(resp: Any, max_bytes: int) -> tuple[bytes, bool]:
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


def _stream_capped_to_file(resp: Any, max_bytes: int, suffix: str) -> tuple[str, bool]:
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


def bounded_get(session: Any, url: str, *, mode: str = "bytes", method: str = "GET",
                headers: dict | None = None, params: dict | None = None,
                timeout: int = 120, proxies: dict | None = None,
                max_bytes: int | None = None, file_suffix: str = ".bin",
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
        except Exception:  # noqa: BLE001  # 关连接失败不该淹没正常返回值
            pass
