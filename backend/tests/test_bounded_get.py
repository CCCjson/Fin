"""
bounded_get 单测 —— 验证响应封顶 + 大文件切块落盘，杜绝无上限读整份响应进内存。
覆盖：bytes 截断 / json 可用性 / Content-Length 短路 / file 落盘+截断 / 正常小响应。
"""
import os

from knowledge_engine.websearch.session import bounded_get, Capped


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._body = body
        self.status_code = status
        self.headers = headers or {}

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


class _FakeSession:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    def request(self, method, url, **kw):
        return self._resp


def test_bytes_small_ok():
    body = b'{"a": 1, "b": [1, 2, 3]}'
    cap = bounded_get(_FakeSession(_FakeResp(body)), "http://x", mode="bytes")
    assert isinstance(cap, Capped)
    assert cap.status == 200
    assert cap.data == body
    assert not cap.truncated and cap.usable_json
    import json
    assert json.loads(cap.data)["a"] == 1


def test_bytes_truncated_marks_json_unusable():
    body = b"x" * 5000
    cap = bounded_get(_FakeSession(_FakeResp(body)), "http://x", mode="bytes", max_bytes=1000)
    assert cap.truncated is True
    assert len(cap.data) == 1000          # 只留到上限
    assert cap.usable_json is False        # JSON 不完整 → 不可解析


def test_content_length_short_circuit():
    # Content-Length 预判超限 → 直接不下载
    body = b"y" * 100
    resp = _FakeResp(body, headers={"content-length": str(999_999_999)})
    cap = bounded_get(_FakeSession(resp), "http://x", mode="bytes", max_bytes=1000)
    assert cap.too_large is True
    assert cap.data is None
    assert cap.usable_json is False


def test_file_mode_spills_and_truncates():
    body = b"z" * 5000
    cap = bounded_get(_FakeSession(_FakeResp(body)), "http://x", mode="file",
                      max_bytes=1000, file_suffix=".pdf")
    try:
        assert cap.path is not None and os.path.exists(cap.path)
        assert os.path.getsize(cap.path) == 1000   # 落盘只到上限
        assert cap.truncated is True
    finally:
        if cap.path:
            os.unlink(cap.path)


def test_file_mode_small_ok():
    body = b"%PDF-1.4 small file"
    cap = bounded_get(_FakeSession(_FakeResp(body)), "http://x", mode="file", file_suffix=".pdf")
    try:
        assert cap.path is not None and os.path.exists(cap.path)
        assert os.path.getsize(cap.path) == len(body)
        assert not cap.truncated
        with open(cap.path, "rb") as f:
            assert f.read(4) == b"%PDF"
    finally:
        if cap.path:
            os.unlink(cap.path)


def test_auto_mode_html_to_memory():
    body = b"<html><title>hi</title></html>"
    resp = _FakeResp(body, headers={"content-type": "text/html; charset=utf-8"})
    cap = bounded_get(_FakeSession(resp), "http://x/page", mode="auto")
    assert cap.path is None and cap.data == body


def test_auto_mode_pdf_to_file():
    body = b"%PDF-1.7 ..."
    resp = _FakeResp(body, headers={"content-type": "application/pdf"})
    cap = bounded_get(_FakeSession(resp), "http://x/doc", mode="auto")
    try:
        assert cap.path is not None and cap.data is None
    finally:
        if cap.path:
            os.unlink(cap.path)
