"""
工具注册表 — @tool 装饰器 + 全局单例 REGISTRY。

把现有同步引擎函数用「瘦适配器」包成 OpenAI function-calling 工具，不改引擎。
工具函数约定返回 dict：
    {"summary": <精简结论，回灌 LLM context>,
     "widget": <可选 WidgetSpec，旁路直推前端，不进 LLM context>}
也允许直接返回任意 dict（executor 会兜底当作 summary 处理）。
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema
    fn: Callable
    category: str
    requires_confirmation: bool = False
    is_subagent: bool = False
    model_hint: Optional[str] = None
    preview_fn: Optional[Callable] = None  # 确认前生成预览（订单详情 + 风控预检）


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, td: ToolDef) -> None:
        if td.name in self._tools:
            raise ValueError(f"工具重名: {td.name}")
        self._tools[td.name] = td

    def get(self, name: str) -> ToolDef:
        if name not in self._tools:
            raise KeyError(f"未注册的工具: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[ToolDef]:
        return list(self._tools.values())

    def subagent_names(self) -> set[str]:
        return {n for n, td in self._tools.items() if td.is_subagent}

    def openai_schemas(self, allowed: Optional[set[str]] = None) -> list[dict]:
        """产出 OpenAI tools 数组。allowed=None 时暴露全部工具。"""
        schemas = []
        for td in self._tools.values():
            if allowed is not None and td.name not in allowed:
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.parameters,
                },
            })
        return schemas


REGISTRY = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    parameters: dict,
    category: str,
    requires_confirmation: bool = False,
    is_subagent: bool = False,
    model_hint: Optional[str] = None,
    preview_fn: Optional[Callable] = None,
) -> Callable:
    """登记一个工具。被装饰函数原样返回（仍可正常调用）。"""

    def deco(fn: Callable) -> Callable:
        REGISTRY.register(ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            fn=fn,
            category=category,
            requires_confirmation=requires_confirmation,
            is_subagent=is_subagent,
            model_hint=model_hint,
            preview_fn=preview_fn,
        ))
        return fn

    return deco
