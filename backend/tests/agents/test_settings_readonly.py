"""
settings_tools.py 风控只读键黑名单回归测试（Phase 1 全量迁移到 args_model + ToolEnvelope）。

_RISK_READONLY_KEYS 六个键必须在 preview（确认前）和 update_setting（执行时）
两处都被拒绝——即便未来 SETTINGS_SCHEMA 白名单意外扩充到包含这些键，这道黑名单
也是第二道防线，不应该被移除或绕过。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")

import pytest  # noqa: E402

from agents.executor import run_tool  # noqa: E402
from agents.tools.settings_tools import _RISK_READONLY_KEYS, _preview_setting  # noqa: E402


@pytest.mark.parametrize("key", sorted(_RISK_READONLY_KEYS))
def test_preview_rejects_risk_readonly_key(key):
    p = _preview_setting({"key": key, "value": "1"})
    assert "error" in p
    assert "风控" in p["error"] or "资金" in p["error"]


@pytest.mark.parametrize("key", sorted(_RISK_READONLY_KEYS))
def test_update_setting_rejects_risk_readonly_key(key):
    r = run_tool("update_setting", {"key": key, "value": "1"})
    assert r["ok"] is True  # 拒绝是诚实的业务性否定，不是工具执行异常
    assert r["business_result"] == "negative"
    assert "只读" in r["summary"] or "无权" in r["summary"]


def test_update_setting_unknown_key_rejected():
    r = run_tool("update_setting", {"key": "not_a_real_setting_key", "value": "x"})
    assert r["business_result"] == "negative"
    assert "未知配置项" in r["summary"]


def test_get_settings_never_leaks_real_secret_values():
    r = run_tool("get_settings", {})
    assert r["ok"] is True
    groups = r["data"]["groups"]
    for g in groups:
        for f in g["fields"]:
            if f.get("sensitive"):
                # field_view 敏感字段：value 恒为空，真实值只能通过 preview 的掩码窥见
                assert f.get("value") == ""
                assert "…" in f.get("preview", "") or f.get("preview", "") == ""
