"""
PolicyChecker 回归测试（Phase 5）。

覆盖：未经工具背书的推荐代码拦截、subagent 收尾长度阈值、以及两个关键的
"不应误伤"场景——只是查询/复述用户已知代码的情形不该被当成违规拦截。
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.policy_checks import PolicyChecker  # noqa: E402


def _assistant_tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}],
    }


def _tool_result(call_id: str, content) -> dict:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _session(messages, turn_start_idx=0):
    return SimpleNamespace(messages=messages, turn_start_idx=turn_start_idx)


def test_unbacked_recommendation_code_is_blocked():
    """本 turn 没有对某代码调用过任何工具，最终回答却提到它——应该拦截。"""
    session = _session([
        {"role": "user", "content": "今天有什么可以买的"},
    ])
    violation = PolicyChecker().check(session, "建议买入600519.SH，仓位5%。")
    assert violation is not None
    assert "600519.SH" in violation


def test_backed_by_tool_call_args_is_allowed():
    """模型对这个代码调用过工具（哪怕不是 recommend_stocks），就不算凭记忆瞎报。"""
    session = _session([
        {"role": "user", "content": "600519.SH 现在怎么样"},
        _assistant_tool_call("get_cockpit_score", {"symbol": "600519.SH"}),
        _tool_result("c1", {"symbol": "600519.SH", "composite": 72}),
    ])
    violation = PolicyChecker().check(session, "600519.SH 综合评分72，技术面偏强。")
    assert violation is None


def test_backed_by_recommend_stocks_result_is_allowed():
    session = _session([
        {"role": "user", "content": "今天有什么可以买的"},
        _assistant_tool_call("recommend_stocks", {}),
        _tool_result("c1", {"buys": [{"symbol": "600519.SH", "name": "贵州茅台"}]}),
    ])
    violation = PolicyChecker().check(session, "推荐600519.SH，综合评级BUY。")
    assert violation is None


def test_no_codes_mentioned_never_triggers():
    session = _session([{"role": "user", "content": "今天大盘怎么样"}])
    violation = PolicyChecker().check(session, "今天大盘震荡，整体偏弱。")
    assert violation is None


def test_subagent_long_tail_is_blocked():
    session = _session([
        {"role": "user", "content": "帮我深度研判一下"},
        _assistant_tool_call("run_deep_stock", {"symbol": "600519.SH"}),
        _tool_result("c1", "deep research streamed already"),
    ])
    long_tail = "这只股票的技术面和基本面都不错，" * 20  # 明显超过阈值
    violation = PolicyChecker().check(session, long_tail)
    assert violation is not None
    assert "一句话" in violation


def test_subagent_short_tail_is_allowed():
    session = _session([
        {"role": "user", "content": "帮我深度研判一下"},
        _assistant_tool_call("run_deep_stock", {"symbol": "600519.SH"}),
        _tool_result("c1", "deep research streamed already"),
    ])
    violation = PolicyChecker().check(session, "研判已完成，如上。")
    assert violation is None


def test_empty_final_text_never_triggers():
    session = _session([{"role": "user", "content": "hi"}])
    assert PolicyChecker().check(session, "") is None
    assert PolicyChecker().check(session, None) is None
