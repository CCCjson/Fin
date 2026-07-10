"""
Subagent 基类 + 通用工具。

每个 SubagentRunner.run(args) 是同步 Generator，yield NDJSON 字符串：
- 过程事件（chunk / agent_progress）直接透传到主聊天流
- 必须以一个 subagent_done 事件收尾，用 emit_subagent_done(envelope) 产出——
  envelope 是 ToolEnvelope（同普通工具的返回信封，subagent 本质是「贵一点的工具」）。
  因为是 pydantic 模型，任何遗漏 ok/error_code/business_result 的构造在开发期就
  会直接抛 ValidationError，不会像旧版 news.py 那样静默漏掉 ok 字段被下游误判成功。
"""
import json
from abc import ABC, abstractmethod
from typing import Generator, Iterable, Optional

from agents.events import EV, emit
from agents.tool_envelope import ErrorCode, ToolEnvelope


class SubagentRunner(ABC):
    name: str

    @abstractmethod
    def run(self, args: dict, cancel_event=None) -> Generator[str, None, None]:
        """cancel_event 默认 None（向后兼容直调）：父 session 断连时被置位，
        耗时子任务据此提前收尾，不再白跑到底。"""
        ...


def emit_subagent_done(envelope: ToolEnvelope) -> str:
    """唯一的 subagent_done 事件产出口——widgets 字段沿用旧 wire 格式（list），
    从 envelope.widget（单个，可选）适配成 [widget] 或 []。"""
    result = envelope.model_dump(exclude_none=True, exclude={"widget"})
    result["widgets"] = [envelope.widget] if envelope.widget else []
    return emit("subagent_done", result=result)


# 正文已经完整流给用户了，回灌进模型 context 的只是一份截断摘要。两种收尾政策：
#
#   NO_RESTATE   —— 主 agent 不该再写一遍（整篇成品已展示，复述只会重复+矛盾）
#   SYNTHESIZE   —— 主 agent 要基于多个章节的结论撰写纵览与操作计划（13.2 拆解后
#                   Ch1「纵览」没有了对应工具，改由 MoneyBill 本人写）
SUMMARY_NO_RESTATE = (
    "[以下报告已完整展示给用户，仅作上下文存档。"
    "收尾时禁止复述其中的指标/价位/结论，禁止另给操作建议]"
)
SUMMARY_SYNTHESIZE = (
    "[以下章节已完整展示给用户，仅作上下文存档。"
    "你可以基于其结论撰写纵览与操作计划，但禁止逐段复述原文]"
)


def relay_markdown(
    inner: Iterable[str],
    *,
    agent: str,
    model: str | None = None,
    max_summary: int = 1200,
    summary_prefix: str = SUMMARY_NO_RESTATE,
) -> Generator[str, None, None]:
    """
    把一个 yield NDJSON 字符串(start/collecting/chunk/done/error)的引擎生成器，
    转成主聊天流：chunk → 直接流式显示；collecting → agent_progress；
    结尾 yield subagent_done（summary = 累积文本截断）。

    summary_prefix 决定主 agent 拿到摘要后被允许做什么，见上面两个常量。
    """
    acc: list[str] = []
    err = None
    tokens = 0
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
            if ev.get("token_count"):
                tokens = int(ev["token_count"])
                if model:
                    from agents.usage import USAGE
                    USAGE.record_total(model, tokens)
        elif et == "error":
            err = ev.get("message", "出错了")

    text = "".join(acc).strip()
    if err and not text:
        # 底层引擎报错且完全没产出——技术性失败，让主 agent 走 ok:False 自愈路径
        envelope = ToolEnvelope(ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                                 error_detail={"message": err},
                                 message=f"[{agent}] 执行失败：{err}", tokens=tokens)
    elif not text:
        # 跑完了但没产出——诚实的"无"，不是异常
        envelope = ToolEnvelope(business_result="negative",
                                 message=f"[{agent}] 没有产生输出", tokens=tokens)
    else:
        # chunk 已全部流式展示给用户，这份摘要只作对话上下文存档；
        # 前缀显式约束主 agent 拿它能做什么，避免它基于截断文本再写一遍（曾导致重复+矛盾建议）
        body = text[:max_summary] + ("…(已截断)" if len(text) > max_summary else "")
        envelope = ToolEnvelope(message=summary_prefix + "\n" + body, tokens=tokens)
    yield emit_subagent_done(envelope)
