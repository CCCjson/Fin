"""
registry.py::ToolDef.openai_schema() 双路径回归测试（Phase 0：新增 args_model 字段，
与旧 parameters 并存，不改变现有工具的实际暴露 schema）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")

from pydantic import BaseModel, Field  # noqa: E402

from agents.registry import REGISTRY, ToolDef  # noqa: E402


def _fn(**kw):
    return {"summary": "ok"}


def test_legacy_parameters_path_unchanged():
    td = ToolDef(
        name="t_legacy_schema",
        description="legacy",
        fn=_fn,
        category="test",
        parameters={"type": "object", "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"]},
    )
    schema = td.openai_schema()
    assert schema == {
        "type": "function",
        "function": {
            "name": "t_legacy_schema",
            "description": "legacy",
            "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}},
                            "required": ["symbol"]},
        },
    }


def test_args_model_path_derives_schema():
    class Args(BaseModel):
        symbol: str = Field(..., description="股票代码")
        target_pct: float = Field(..., gt=0, le=100)

    td = ToolDef(name="t_args_model_schema", description="new", fn=_fn,
                 category="test", args_model=Args)
    schema = td.openai_schema()
    params = schema["function"]["parameters"]
    assert params["type"] == "object"
    assert "symbol" in params["properties"]
    assert "target_pct" in params["properties"]
    assert params["required"] == ["symbol", "target_pct"]
    # OpenAI schema 不需要 pydantic 自动加的 title 噪音
    assert "title" not in params
    assert "title" not in params["properties"]["symbol"]


def test_args_model_takes_priority_over_parameters():
    class Args(BaseModel):
        symbol: str

    td = ToolDef(name="t_priority_schema", description="both", fn=_fn, category="test",
                 parameters={"type": "object", "properties": {"legacy_only": {"type": "string"}}},
                 args_model=Args)
    schema = td.openai_schema()
    assert "legacy_only" not in schema["function"]["parameters"]["properties"]
    assert "symbol" in schema["function"]["parameters"]["properties"]


def test_no_schema_source_falls_back_to_empty_object():
    td = ToolDef(name="t_empty_schema", description="none", fn=_fn, category="test")
    schema = td.openai_schema()
    assert schema["function"]["parameters"] == {"type": "object", "properties": {}}


def test_full_registry_openai_schemas_all_valid_function_shape():
    """现有真实注册表里的每个工具 schema 结构不变（回归安全网，Phase 1/2 迁移期持续跑）。"""
    for schema in REGISTRY.openai_schemas():
        assert schema["type"] == "function"
        fn = schema["function"]
        assert set(fn.keys()) == {"name", "description", "parameters"}
        assert isinstance(fn["parameters"], dict)


def test_nested_model_titles_are_stripped_too():
    """code-review 发现：旧版只剥顶层 + 顶层 properties 的 title，嵌套 BaseModel
    展开进 $defs 后自己的 title 会漏网（screen_stocks 的 ScreenFilterItem 就是
    实例）。回归锁死：整棵 schema 树里不应该再出现任何 title 键。"""
    class Inner(BaseModel):
        field: str = Field(..., description="x")

    class Outer(BaseModel):
        items: list[Inner] = Field(..., description="y")

    td = ToolDef(name="t_nested_schema", description="nested", fn=_fn,
                 category="test", args_model=Outer)
    schema = td.openai_schema()

    def _find_titles(node) -> list:
        found = []
        if isinstance(node, dict):
            if "title" in node:
                found.append(node["title"])
            for v in node.values():
                found.extend(_find_titles(v))
        elif isinstance(node, list):
            for item in node:
                found.extend(_find_titles(item))
        return found

    assert _find_titles(schema) == []
