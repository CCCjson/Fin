"""
executor.py::run_tool 参数校验回归测试（Phase 1）。

覆盖 Jason 要求的核心机制：
- args_model 存在时，pydantic ValidationError 拦截 fn 执行，回灌结构化错误（不是异常字符串）
- 校验通过后 fn 正常跑，ToolEnvelope 返回值被正确映射成 legacy dict
- 未迁移工具（无 args_model）行为完全不变（TypeError/Exception 兜底路径）
- ToolEnvelope 内部异常（INTERNAL_ERROR）与参数校验异常（VALIDATION_ERROR）分类清楚
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")

from pydantic import BaseModel, Field  # noqa: E402

from agents.executor import run_tool  # noqa: E402
from agents.registry import REGISTRY, ToolDef  # noqa: E402
from agents.tool_envelope import ToolEnvelope  # noqa: E402


class _Args(BaseModel):
    symbol: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0, le=100)


def _ensure_tools():
    if not REGISTRY.has("t_exec_validated"):
        REGISTRY.register(ToolDef(
            name="t_exec_validated", description="校验工具", fn=lambda symbol, qty: ToolEnvelope(
                data={"symbol": symbol, "qty": qty}),
            category="test", args_model=_Args,
        ))
    if not REGISTRY.has("t_exec_legacy"):
        def _legacy_fn(symbol: str):
            return {"summary": f"legacy:{symbol}"}
        REGISTRY.register(ToolDef(
            name="t_exec_legacy", description="旧工具", fn=_legacy_fn, category="test",
            parameters={"type": "object", "properties": {"symbol": {"type": "string"}},
                        "required": ["symbol"]},
        ))
    if not REGISTRY.has("t_exec_boom"):
        def _boom(symbol: str):
            raise KeyError("db gone")
        REGISTRY.register(ToolDef(
            name="t_exec_boom", description="内部异常", fn=_boom, category="test",
            args_model=type("BoomArgs", (BaseModel,), {"__annotations__": {"symbol": str}}),
        ))


_ensure_tools()


def test_validation_error_blocks_execution_and_is_retryable_shape():
    r = run_tool("t_exec_validated", {"symbol": "", "qty": 200})
    assert r["ok"] is False
    assert r["error_code"] == "validation_error"
    assert "errors" in r["error_detail"]
    fields = {e["field"] for e in r["error_detail"]["errors"]}
    assert "symbol" in fields and "qty" in fields
    # summary 必须是可读文本（回灌 LLM），不是原始 Python 异常字符串
    assert "参数校验失败" in r["summary"]


def test_valid_args_execute_and_envelope_maps_to_legacy_dict():
    r = run_tool("t_exec_validated", {"symbol": "600519.SH", "qty": 10})
    assert r["ok"] is True
    assert r["business_result"] == "affirmative"
    assert "600519.SH" in r["summary"]


def test_legacy_tool_without_args_model_unaffected():
    r = run_tool("t_exec_legacy", {"symbol": "600519.SH"})
    assert r["ok"] is True
    assert r["summary"] == "legacy:600519.SH"
    assert "error_code" not in r


def test_legacy_tool_type_error_path_unchanged():
    r = run_tool("t_exec_legacy", {"wrong_kw": 1})
    assert r["ok"] is False
    assert "参数错误" in r["summary"]


def test_internal_exception_classified_as_internal_error():
    r = run_tool("t_exec_boom", {"symbol": "600519.SH"})
    assert r["ok"] is False
    assert r["error_code"] == "internal_error"
    assert r["error_detail"]["exception_type"] == "KeyError"


def test_unknown_tool_name():
    r = run_tool("t_does_not_exist", {})
    assert r["ok"] is False
    assert "summary" in r
