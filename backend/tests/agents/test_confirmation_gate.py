"""
确认门相关回归测试（Phase 1 视角：place_order 迁移到 ToolEnvelope 后，
风控仍然不可绕过）。

不触碰真实数据库/真实撮合——只验证风控拒单分支（在任何 DB 写入之前就返回）、
以及 `_confirmed` 门闩标记经 args_model 校验后被静默丢弃、不会让工具函数本身
看到或依赖它（风控检查本来就与 _confirmed 无关，无条件重跑）。

orchestrator 层的 `_sanitize_tool_args`/`resume_with_confirmation` 编排逻辑
已由 tests/test_agent_safety.py 覆盖，这里只覆盖 Phase 1 改动到的
executor+trading_tools 这一段。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")

import inspect  # noqa: E402

from agents.executor import run_tool  # noqa: E402
from agents.tools import trading_tools  # noqa: E402


def test_place_order_signature_has_no_confirm_plumbing_param():
    """args_model 迁移后，kwargs 严格等于声明字段——不再需要 _confirmed/**_kw 兜底吞参。"""
    sig = inspect.signature(trading_tools.place_order)
    assert "_confirmed" not in sig.parameters
    assert not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def test_confirmed_flag_is_silently_ignored_by_validation_not_forged_into_behavior(monkeypatch):
    """resume_with_confirmation 会往 args 里塞 _confirmed=True 再调 run_tool；
    args_model 校验会把这个非声明字段丢弃（extra=ignore），工具函数根本看不到它——
    风控判断只能来自 _risk_check 的真实结果，不存在"看见 _confirmed 就放行"的分支。"""
    monkeypatch.setattr(trading_tools, "_risk_check",
                         lambda symbol, action, qty, price, broker_info: (False, ["测试：模拟风控拒绝"], ["测试：模拟风控拒绝"]))
    monkeypatch.setattr(trading_tools, "_paper_broker_info", lambda: (None, {"cash": 100000}))

    r_with_confirm = run_tool("place_order", {
        "symbol": "600519.SH", "side": "buy", "quantity": 100, "price": 100.0,
        "_confirmed": True,
    })
    r_without_confirm = run_tool("place_order", {
        "symbol": "600519.SH", "side": "buy", "quantity": 100, "price": 100.0,
    })
    # 两种调用方式风控结论完全一致：_confirmed 存在与否不影响风控判断本身
    assert r_with_confirm["business_result"] == "negative"
    assert r_without_confirm["business_result"] == "negative"
    assert r_with_confirm["ok"] is True  # 风控拒单是诚实的"否"，不是工具执行异常
    assert "风控" in str(r_with_confirm["data"])


def test_risk_rejection_returns_before_any_db_write(monkeypatch):
    """风控不过时应在任何撮合/DB 写入之前就返回——用一个会立刻失败的哨兵替换
    _paper_broker_info 之后的所有下游调用点，若代码路径越过风控检查就会在这里炸。"""
    monkeypatch.setattr(trading_tools, "_risk_check",
                         lambda symbol, action, qty, price, broker_info: (False, ["拒绝"], ["拒绝"]))
    monkeypatch.setattr(trading_tools, "_paper_broker_info",
                         lambda: (None, {"cash": 100000}))  # 只需给到风控检查前的最小依赖
    r = run_tool("place_order", {"symbol": "600519.SH", "side": "buy",
                                  "quantity": 100, "price": 100.0})
    assert r["business_result"] == "negative"
    assert r["data"]["executed"] is False
