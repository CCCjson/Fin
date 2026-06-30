"""
Subagent 基类 + 通用工具。

每个 SubagentRunner.run(args) 是同步 Generator，yield NDJSON 字符串：
- 过程事件（chunk / agent_progress）直接透传到主聊天流
- 必须以一个 subagent_done 事件收尾，携带 {"result": {"summary": str, "widgets": [...]}}
  summary 回灌给 Monitor（精简），widgets 旁路推前端。
"""
import json
from abc import ABC, abstractmethod
from typing import Generator, Iterable

from agents.events import EV, emit


class SubagentRunner(ABC):
    name: str

    @abstractmethod
    def run(self, args: dict) -> Generator[str, None, None]:
        ...


def relay_markdown(
    inner: Iterable[str],
    *,
    agent: str,
    model: str | None = None,
    max_summary: int = 1200,
) -> Generator[str, None, None]:
    """
    把一个 yield NDJSON 字符串(start/collecting/chunk/done/error)的引擎生成器，
    转成主聊天流：chunk → 直接流式显示；collecting → agent_progress；
    结尾 yield subagent_done（summary = 累积文本截断）。
    """
    acc: list[str] = []
    err = None
    for line in inner:
        line = (line or "").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = ev.get("event")
        if et == "chunk":
            c = ev.get("content", "")
            if c:
                acc.append(c)
                yield emit(EV.CHUNK, content=c)
        elif et == "collecting":
            yield emit(EV.AGENT_PROGRESS, agent=agent, message=ev.get("message", ""))
        elif et == "done":
            if model and ev.get("token_count"):
                from agents.usage import USAGE
                USAGE.record_total(model, int(ev["token_count"]))
        elif et == "error":
            err = ev.get("message", "出错了")

    text = "".join(acc).strip()
    if err and not text:
        summary = f"[{agent}] 执行失败：{err}"
    else:
        summary = text[:max_summary] + ("…(已截断)" if len(text) > max_summary else "")
        if not summary:
            summary = f"[{agent}] 没有产生输出"
    yield emit("subagent_done", result={"summary": summary, "widgets": []})
