"""
工具注册表 — @tool 装饰器 + 全局单例 REGISTRY。

把现有同步引擎函数用「瘦适配器」包成 OpenAI function-calling 工具，不改引擎。
工具函数约定返回 dict：
    {"summary": <精简结论，回灌 LLM context>,
     "widget": <可选 WidgetSpec，旁路直推前端，不进 LLM context>}
也允许直接返回任意 dict（executor 会兜底当作 summary 处理）。

参数 schema 迁移中：新工具用 args_model（pydantic BaseModel）声明参数，
单一数据源同时承担「运行时校验」（见 executor.py）和「生成 OpenAI function-calling
schema」两个角色。旧工具仍可用手写 parameters（JSON Schema dict），两条路径并存，
args_model 存在时优先；全量迁移完成后会删除 parameters 路径（见迁移计划 Phase 2）。
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

from pydantic import BaseModel


def _strip_titles(node: object) -> None:
    """递归剥掉 pydantic model_json_schema() 生成的 title 噪音——不止顶层和顶层
    properties，嵌套 BaseModel 展开进 $defs 后自己的 title/properties[*].title
    也要剥（比如 ScreenStocksArgs.filters: list[ScreenFilterItem]），否则嵌套
    模型的冗余 title 会一直漏网，每轮都在往 LLM schema 里塞没用的 token。"""
    if isinstance(node, dict):
        node.pop("title", None)
        for v in node.values():
            _strip_titles(v)
    elif isinstance(node, list):
        for item in node:
            _strip_titles(item)


@dataclass
class ToolDef:
    name: str
    description: str
    fn: Callable
    category: str
    parameters: Optional[dict] = None       # 旧：手写 JSON Schema（迁移期与 args_model 并存）
    args_model: Optional[type[BaseModel]] = None  # 新：pydantic 模型，校验+schema 单一数据源
    group: Optional[str] = None             # 归组（吞并 tool_groups.py 手写清单，见 Phase 9）
    requires_confirmation: bool = False
    is_subagent: bool = False
    model_hint: Optional[str] = None
    preview_fn: Optional[Callable] = None  # 确认前生成预览（订单详情 + 风控预检）

    def openai_schema(self) -> dict:
        """产出该工具的 OpenAI function-calling schema。args_model 优先于手写 parameters。"""
        if self.args_model is not None:
            schema = self.args_model.model_json_schema()
            _strip_titles(schema)
            params = schema
        else:
            params = self.parameters or {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }


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
            schemas.append(td.openai_schema())
        return schemas


REGISTRY = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    category: str,
    parameters: Optional[dict] = None,
    args_model: Optional[type[BaseModel]] = None,
    group: Optional[str] = None,
    requires_confirmation: bool = False,
    is_subagent: bool = False,
    model_hint: Optional[str] = None,
    preview_fn: Optional[Callable] = None,
) -> Callable:
    """登记一个工具。被装饰函数原样返回（仍可正常调用）。

    parameters（手写 JSON Schema）与 args_model（pydantic 模型）二选一：
    新工具用 args_model，旧工具迁移完成前继续用 parameters。两者都不给会在
    openai_schema() 里退化成空 object schema，不会报错但对 LLM 无意义，应避免。
    """

    def deco(fn: Callable) -> Callable:
        REGISTRY.register(ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            args_model=args_model,
            group=group,
            fn=fn,
            category=category,
            requires_confirmation=requires_confirmation,
            is_subagent=is_subagent,
            model_hint=model_hint,
            preview_fn=preview_fn,
        ))
        return fn

    return deco
