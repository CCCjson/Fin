"""跨测试目录共享的夹具工厂（不是 conftest —— 显式 import，不带环境副作用）。

`tests/agents/conftest.py` 与 `tests/baseline/conftest.py` 各自 import 本模块。
环境变量（AGENT_TOOL_GROUPS 等）留在各自的 conftest 里设，不上移到根，
否则会改变所有测试的运行环境。
"""
import json
from collections.abc import Callable
from typing import Any


def script_stream_chat(script: list) -> Callable[..., Any]:
    """按脚本逐次返回的 fake stream_chat。

    Args:
        script: 每个元素代表一轮 LLM 响应。`None` = 只吐纯文本收尾；
            `[{"id":..., "name":..., "args": {...}}, ...]` = 吐 tool_calls。

    Returns:
        签名兼容真实 `stream_chat(messages, model=None, tools=None, **kw)` 的生成器函数。
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


def drain_subagent_done(gen) -> dict:
    """跑完 subagent 生成器，取最后一条 subagent_done 事件的 result。

    subagent 的对外契约就是「必须以恰好一个 subagent_done 收尾」，
    所以这个 helper 顺便断言了 done 事件存在。
    """
    done = [json.loads(line) for line in gen if '"subagent_done"' in line]
    assert len(done) == 1, f"应恰好产出 1 条 subagent_done，实际 {len(done)} 条"
    return done[0]["result"]
