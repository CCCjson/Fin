"""13.2-2 行为基线：llm_client.stream_text()。

纯文本流式的唯一实现（消灭 report/advisor/news/alpha 四处手抄的 create(stream=True) 循环）。
锁：文本增量、token 覆盖取最终值、cancel 提前 close、max_tokens 仅 >0 下发、
stream_options 不支持时回退。全部用 fake client，不打真实 LLM。
"""
import threading
import types

import pytest

from llm_client import stream_text

pytestmark = pytest.mark.baseline


def _chunk(content=None, usage=None):
    delta = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(delta=delta)
    return types.SimpleNamespace(choices=[choice] if content is not None else [], usage=usage)


def _usage(total, prompt=0, completion=0):
    return types.SimpleNamespace(total_tokens=total, prompt_tokens=prompt, completion_tokens=completion)


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False
        self.consumed = 0

    def __iter__(self):
        for c in self._chunks:
            if self.closed:
                return
            self.consumed += 1
            yield c

    def close(self):
        self.closed = True


class _FakeClient:
    """记录最后一次 create 的 kwargs；可配置是否支持 stream_options。"""

    def __init__(self, chunks, supports_stream_options=True):
        self._chunks = chunks
        self.supports_stream_options = supports_stream_options
        self.calls: list[dict] = []
        self.stream: _FakeStream | None = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                if "stream_options" in kwargs and not outer.supports_stream_options:
                    raise TypeError("unsupported stream_options")
                outer.calls.append(kwargs)
                outer.stream = _FakeStream(outer._chunks)
                return outer.stream

        self.chat = types.SimpleNamespace(completions=_Completions())


def test_yields_text_increments_then_done():
    client = _FakeClient([_chunk("你"), _chunk("好"), _chunk(usage=_usage(150, 100, 50))])
    events = list(stream_text([{"role": "user", "content": "hi"}], model="gpt-5.4-mini", client=client))

    assert [e["content"] for e in events if e["type"] == "text"] == ["你", "好"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["content"] == "你好"
    assert done["cancelled"] is False


def test_usage_is_overwritten_not_accumulated():
    """OpenAI 只在最后一个 chunk 发非空 usage；+= 会在同 chunk 内双计。"""
    client = _FakeClient([
        _chunk("a", usage=_usage(10, 6, 4)),
        _chunk("b"),
        _chunk(usage=_usage(150, 100, 50)),
    ])
    done = list(stream_text([], model="m", client=client))[-1]
    assert done["tokens"] == 150
    assert done["prompt_tokens"] == 100
    assert done["completion_tokens"] == 50


def test_cancel_event_closes_stream_and_stops_early():
    """断连后不该继续烧 token——关流跳出，而不是等这轮吐完。"""
    cancel = threading.Event()
    client = _FakeClient([_chunk("a"), _chunk("b"), _chunk("c"), _chunk("d")])

    out = []
    gen = stream_text([], model="m", client=client, cancel_event=cancel)
    for ev in gen:
        if ev["type"] == "text":
            out.append(ev["content"])
            if len(out) == 2:
                cancel.set()
        else:
            done = ev

    assert out == ["a", "b"]
    assert client.stream.closed is True
    assert client.stream.consumed < 4
    assert done["cancelled"] is True
    assert done["content"] == "ab"


def test_cancel_before_first_chunk_yields_nothing_but_done():
    cancel = threading.Event()
    cancel.set()
    client = _FakeClient([_chunk("a"), _chunk("b")])
    events = list(stream_text([], model="m", client=client, cancel_event=cancel))
    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert events[0]["cancelled"] is True
    assert events[0]["content"] == ""


def test_max_tokens_only_sent_when_positive():
    client = _FakeClient([_chunk("x")])
    list(stream_text([], model="m", client=client))
    assert "max_tokens" not in client.calls[-1]

    client = _FakeClient([_chunk("x")])
    list(stream_text([], model="m", client=client, max_tokens=4096))
    assert client.calls[-1]["max_tokens"] == 4096


def test_gpt5_family_params_normalized():
    """GPT-5 不认 max_tokens / 自定义 temperature，交给 normalize_chat_params。"""
    client = _FakeClient([_chunk("x")])
    list(stream_text([], model="gpt-5.5", client=client, max_tokens=2048, temperature=0.3))
    kw = client.calls[-1]
    assert kw["max_completion_tokens"] == 2048
    assert "max_tokens" not in kw
    assert "temperature" not in kw


def test_falls_back_when_stream_options_unsupported():
    client = _FakeClient([_chunk("x")], supports_stream_options=False)
    events = list(stream_text([], model="m", client=client))
    assert events[0]["content"] == "x"
    assert "stream_options" not in client.calls[-1]


def test_no_tools_ever_sent():
    """stream_text 是纯文本通道；带工具请用 stream_chat。"""
    client = _FakeClient([_chunk("x")])
    list(stream_text([], model="m", client=client))
    assert "tools" not in client.calls[-1]
    assert client.calls[-1]["stream"] is True
