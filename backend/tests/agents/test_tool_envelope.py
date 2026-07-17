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


# ════════════════ 数据质量维度（P0-2） ════════════════

_DEGRADED = {"overall_score": 50, "level": "poor", "core_degraded": True,
             "limitations": ["quote: stale"], "block_scores": {"quote": 50},
             "version": "context-quality-v1"}
_HEALTHY = {"overall_score": 100, "level": "good", "core_degraded": False,
            "limitations": [], "block_scores": {"quote": 100},
            "version": "context-quality-v1"}


def test_quality_warning_is_prefixed_not_buried():
    """🔒 质量警告必须**前置**在回灌文本最前面。

    两个坑一起绕（都实测过）：
    1. `truncate_json_safe` 按插入序保前缀键、超限就 break —— quality 塞在 data
       尾部时，大结果一来就被静默丢掉，只留一行「丢弃字段」。数据质量警告恰恰
       是最不能丢的那个。
    2. `to_legacy_dict` 里 message 优先于 data —— 工具只要给了 message，整个
       data 就不进 summary。

    别把它改回「data 里的一个键」。
    """
    from agents.tool_envelope import to_legacy_dict
    env = ToolEnvelope(data={"symbol": "600519.SH", "price": 1253.0},
                        quality=_DEGRADED)
    out = to_legacy_dict(env)
    assert out["summary"].startswith("⚠️ 数据质量降级")
    assert "quote: stale" in out["summary"]
    assert "不得声称高置信度" in out["summary"]


def test_quality_warning_survives_message_priority():
    """🔒 工具给了 message 时，质量警告照样要能到达 LLM。"""
    from agents.tool_envelope import to_legacy_dict
    env = ToolEnvelope(business_result="negative", message="未获取到实时行情",
                        quality=_DEGRADED)
    out = to_legacy_dict(env)
    assert out["summary"].startswith("⚠️ 数据质量降级")
    assert "未获取到实时行情" in out["summary"]


def test_healthy_quality_adds_no_noise():
    """🔒 数据健康时一个字都不加。

    每轮每个工具都挂一句「数据正常」是纯 token 浪费，而且狼来了喊多了没人听。
    """
    from agents.tool_envelope import to_legacy_dict
    env = ToolEnvelope(data={"symbol": "600519.SH"}, quality=_HEALTHY)
    out = to_legacy_dict(env)
    assert not out["summary"].startswith("⚠️")
    assert out["quality"] == _HEALTHY        # 结构化字段照常给下游


def test_no_quality_is_unchanged():
    """没传 quality 的工具（多数）行为完全不变。"""
    from agents.tool_envelope import to_legacy_dict
    out = to_legacy_dict(ToolEnvelope(data={"a": 1}))
    assert "quality" not in out
    assert out["summary"] == '{"a": 1}'


def test_quality_is_orthogonal_to_ok_and_business_result():
    """🔒 三个维度正交：成功返回了一个三天前的收盘价 —— 三个维度都用得上。"""
    env = ToolEnvelope(ok=True, business_result="affirmative", quality=_DEGRADED)
    assert env.ok is True
    assert env.is_bad() is False              # 技术上没失败、业务上有结果
    assert env.quality["core_degraded"] is True   # 但数据不可信
