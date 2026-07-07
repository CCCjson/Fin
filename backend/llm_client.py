"""
LLM 客户端封装 — 全后端首个 function-calling + 流式 tool_calls 分片重组。

复用 llm_config.normalize_chat_params（GPT-5 参数兼容）；client 构造沿用
advisor_engine / alpha_lab 的范式（含 Claude 中转站清 x-stainless-* header、
stream_options 不支持时回退）。
"""
import json
import os
import threading
from typing import Generator, Optional

from loguru import logger
from openai import OpenAI
from httpx import Timeout as HttpxTimeout

from llm_config import normalize_chat_params


def build_client(base_url: Optional[str] = None, api_key: Optional[str] = None) -> OpenAI:
    """构造 OpenAI 兼容 client。非官方域名自动清理 x-stainless-* header（中转站防拦）。"""
    base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    api_key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未配置")

    from net_proxy import make_httpx_client
    _timeout = HttpxTimeout(connect=15.0, read=300.0, write=30.0, pool=30.0)
    kwargs = dict(
        api_key=api_key,
        base_url=base_url,
        timeout=_timeout,
        max_retries=2,
        # auto 代理：Clash 在走 Clash、不在直连，不死绑 Clash（去新加坡/本地直翻也能用）
        http_client=make_httpx_client(timeout=_timeout),
    )
    if "api.openai.com" not in base_url:
        kwargs["default_headers"] = {
            "User-Agent": "python-httpx/0.27",
            "x-stainless-lang": "",
            "x-stainless-os": "",
            "x-stainless-arch": "",
            "x-stainless-runtime": "",
            "x-stainless-runtime-version": "",
            "x-stainless-package-version": "",
        }
    return OpenAI(**kwargs)


class _ToolCallAccumulator:
    """把流式 tool_calls 分片按 index 累加重组成完整 tool_call。"""

    def __init__(self) -> None:
        self._by_index: dict[int, dict] = {}

    def add(self, delta_tool_calls) -> None:
        for tc in delta_tool_calls:
            idx = tc.index
            slot = self._by_index.setdefault(
                idx, {"id": None, "name": None, "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

    def finalize(self) -> list[dict]:
        """返回 [{id, name, args(dict)}]，按 index 排序。"""
        result = []
        for idx in sorted(self._by_index):
            slot = self._by_index[idx]
            raw = slot["arguments"] or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"tool_call 参数 JSON 解析失败，原文: {raw!r}")
                args = {}
            result.append({
                "id": slot["id"] or f"call_{idx}",
                "name": slot["name"] or "",
                "args": args,
            })
        return result

    @property
    def empty(self) -> bool:
        return not self._by_index


def stream_chat(
    messages: list[dict],
    *,
    model: str,
    tools: Optional[list[dict]] = None,
    temperature: float = 0.5,
    client: Optional[OpenAI] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[dict, None, None]:
    """
    流式调用 chat.completions。

    Yields（内部协议，供 orchestrator 消费）：
      {"type": "text", "content": "..."}            — 助手文本增量
      {"type": "done", "message": {...}, "tool_calls": [...], "tokens": int}
        message: OpenAI 格式 assistant message（含 tool_calls 时带 tool_calls 字段）
        tool_calls: [{id, name, args}]，无工具调用时为 []

    cancel_event: 客户端断连时会被置位（见 agents/context.py::AgentSession）。命中时
    立即关闭底层流并跳出循环——大多数 OpenAI 兼容端点会因连接断开而提前停止生成，
    真的省下后续 token，而不是等这轮吐完字才在下一轮开头才发现该停。
    """
    client = client or build_client()

    kwargs = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    kwargs = normalize_chat_params(kwargs)

    try:
        stream = client.chat.completions.create(
            **kwargs, stream_options={"include_usage": True})
    except Exception:
        # 部分兼容端点不支持 stream_options，回退
        stream = client.chat.completions.create(**kwargs)

    full_content = ""
    token_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    acc = _ToolCallAccumulator()

    for chunk in stream:
        if cancel_event is not None and cancel_event.is_set():
            try:
                stream.close()
            except Exception:
                pass
            break
        if getattr(chunk, "usage", None):
            token_count = chunk.usage.total_tokens
            prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        if delta.content:
            full_content += delta.content
            yield {"type": "text", "content": delta.content}
        if getattr(delta, "tool_calls", None):
            acc.add(delta.tool_calls)

    tool_calls = acc.finalize()

    # 构造可回灌 messages 的 assistant message（OpenAI 格式）
    if tool_calls:
        message = {
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ],
        }
    else:
        message = {"role": "assistant", "content": full_content}

    yield {"type": "done", "message": message, "tool_calls": tool_calls,
           "tokens": token_count, "prompt_tokens": prompt_tokens,
           "completion_tokens": completion_tokens}
