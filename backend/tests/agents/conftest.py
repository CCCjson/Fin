"""
Harness 公共 fixture —— FakeLLM（脚本化 stream_chat）+ 假工具注册辅助。

约定与 tests/test_agent_safety.py 一致：不打真实 LLM，全部 monkeypatch。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# 工具分组校验要求全量工具归组，测试注册的假工具会破坏校验 → 关掉分组
os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import json  # noqa: E402
import pytest  # noqa: E402


def script_stream_chat(script: list):
    """按脚本逐次返回的 fake stream_chat。script 元素 = tool_calls 列表或 None(纯文本)。

    与 test_agent_safety.py::_script_stream_chat 同构，供新 harness 测试复用。
    """
    it = iter(script)

    def fake(messages, model=None, tools=None, **kw):
        step = next(it)
        if step is None:
            yield {"type": "text", "content": "好的"}
            yield {"type": "done", "message": {"role": "assistant", "content": "好的"},
                   "tool_calls": [], "prompt_tokens": 10, "completion_tokens": 5}
        else:
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": tc["id"], "type": "function",
                                   "function": {"name": tc["name"],
                                                "arguments": json.dumps(tc["args"])}}
                                  for tc in step]}
            yield {"type": "done", "message": msg,
                   "tool_calls": [{"id": tc["id"], "name": tc["name"], "args": tc["args"]}
                                  for tc in step],
                   "prompt_tokens": 10, "completion_tokens": 5}

    return fake


@pytest.fixture
def fake_stream_chat():
    return script_stream_chat
