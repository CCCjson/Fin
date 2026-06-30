"""
MoneyBill multi-agent 编排层。

主编排 Agent（对外身份 MoneyBill，类名 MonitorOrchestrator）用 OpenAI function
calling 直接调用 ~40 个工具完成任务；只有 4 个复杂任务下放给 subagent。

设计要点见 ~/.claude/plans/pure-imagining-quasar.md：
- 工具 = 瘦适配器包现有同步引擎函数 + run_in_executor，不改引擎
- 复用现有 NDJSON 流式协议 + thread/queue 桥接（参考 api/routes/advisor.py）
- 分档模型：Monitor 用 best(gpt-5.5)，subagent 用 cheap(gpt-5.4-mini)
"""

# 触发工具注册副作用：import agents 时自动登记所有工具 + subagent
from agents import tools as _tools  # noqa: F401,E402
from agents import subagents as _subagents  # noqa: F401,E402

__all__ = ["tools", "subagents"]
