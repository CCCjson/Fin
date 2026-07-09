"""
Harness 公共 fixture —— FakeLLM（脚本化 stream_chat）+ 假工具注册辅助。

约定与 tests/test_agent_safety.py 一致：不打真实 LLM，全部 monkeypatch。
工厂本体在 tests/_fixtures.py，与 tests/baseline/ 共用；这里只留环境副作用。
"""
import os

# 工具分组校验要求全量工具归组，测试注册的假工具会破坏校验 → 关掉分组
os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import pytest  # noqa: E402

from tests._fixtures import script_stream_chat  # noqa: E402,F401  （供本目录测试直接 import）


@pytest.fixture
def fake_stream_chat():
    return script_stream_chat
