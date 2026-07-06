"""
ToolEnvelope 构造/序列化边界测试。

覆盖 Jason 要求的核心不变量：ok（技术态）与 business_result（业务态）正交，
error_code 只在 ok=False 时有意义 —— 这两条在 pydantic 层面就该拦住语义坍缩
（比如 news.py 曾经出现过的"漏了 ok 字段被下游默认当成功"这类 bug）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from agents.tool_envelope import ErrorCode, ToolEnvelope  # noqa: E402


def test_defaults_are_affirmative_ok():
    env = ToolEnvelope()
    assert env.ok is True
    assert env.error_code is None
    assert env.business_result == "affirmative"
    assert env.is_bad() is False


def test_negative_business_result_is_not_an_error():
    """风控拒单/未找到数据：ok 仍是 True，只是 business_result=negative。"""
    env = ToolEnvelope(business_result="negative", data={"reason": "风控未通过"})
    assert env.ok is True
    assert env.error_code is None
    assert env.is_bad() is True  # 给 TurnMonitor 判定用，但不是"错误"


def test_validation_error_requires_error_code():
    env = ToolEnvelope(ok=False, error_code=ErrorCode.VALIDATION_ERROR,
                        error_detail={"errors": [{"field": "target_pct", "expected": "le=100",
                                                   "received": 150, "msg": "超出范围"}]})
    assert env.ok is False
    assert env.error_code == ErrorCode.VALIDATION_ERROR
    assert env.is_bad() is True


def test_internal_error_requires_error_code():
    env = ToolEnvelope(ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                        error_detail={"exception_type": "KeyError", "message": "boom"})
    assert env.ok is False
    assert env.is_bad() is True


def test_ok_false_without_error_code_rejected():
    """这是本次改造要堵住的坍缩场景：technical failure 却不说明原因。"""
    with pytest.raises(ValidationError):
        ToolEnvelope(ok=False)


def test_ok_true_with_error_code_rejected():
    """error_code 只在 ok=False 时有意义，误设时应直接在构造期报错。"""
    with pytest.raises(ValidationError):
        ToolEnvelope(ok=True, error_code=ErrorCode.INTERNAL_ERROR)


def test_serialization_roundtrip():
    env = ToolEnvelope(business_result="negative", data={"symbol": "600519.SH"},
                        message="今日无信号", widget={"type": "metric_cards"})
    dumped = env.model_dump(exclude_none=True)
    assert dumped["business_result"] == "negative"
    assert "error_code" not in dumped
    restored = ToolEnvelope.model_validate(dumped)
    assert restored == env
