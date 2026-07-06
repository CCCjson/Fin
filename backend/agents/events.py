"""
Agent 事件协议 — 统一产 NDJSON 行（延续全后端 {"event": ...} 一行一个 JSON 的约定）。

前后端共享这套语义；前端 agentService 按 event 字段分发。
"""
import json
from typing import Any


class EV:
    """事件类型常量。"""

    # 会话生命周期
    SESSION_CREATED = "session_created"
    START = "start"
    DONE = "done"
    ERROR = "error"

    # 助手文本增量（前端无缝复用 advisor 的 chunk 渲染）
    CHUNK = "chunk"

    # 工具调用过程（中间过程可视化）
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    # subagent 派单与子进度
    AGENT_HANDOFF = "agent_handoff"
    AGENT_PROGRESS = "agent_progress"

    # 结构化结果卡片/图表
    WIDGET = "widget"

    # agent 驱动前端导航（打开某功能页并预填）
    NAVIGATE = "navigate"

    # 交易二次确认
    CONFIRM_REQUIRED = "confirm_required"
    AWAIT_CONFIRM = "await_confirm"

    # token 用量 & 成本
    USAGE = "usage"

    # 工具调用流程监控（kind=intervention 干预 / kind=turn_summary 回合汇总）
    MONITOR = "monitor"


def emit(event: str, **fields: Any) -> str:
    """构造一行 NDJSON 事件。"""
    payload = {"event": event, **fields}
    return json.dumps(payload, ensure_ascii=False) + "\n"


def emit_obj(obj: dict) -> str:
    """把已经是 dict 的对象序列化为 NDJSON 行（用于透传子流原始事件）。"""
    return json.dumps(obj, ensure_ascii=False) + "\n"
