"""
第三批修复回归测试：JSON 感知截断（M1）、session 持久化（M2/M4）、verdict hooks（N2）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["AGENT_TOOL_GROUPS"] = "off"

from agents.executor import truncate_json_safe  # noqa: E402
from agents.context import AgentSession, SessionStore  # noqa: E402
import agents.context as ctx  # noqa: E402


# ──────────────────── M1: JSON 感知截断 ────────────────────

def test_truncate_passthrough():
    assert truncate_json_safe("短文本", 100) == "短文本"


def test_truncate_list_keeps_items_intact():
    data = [{"symbol": f"60051{i}.SH", "price": 1234.56 + i} for i in range(50)]
    text = json.dumps(data, ensure_ascii=False)
    out = truncate_json_safe(text, 300)
    body = out.split("\n…")[0]
    kept = json.loads(body)          # 必须是合法 JSON
    assert 0 < len(kept) < 50
    assert kept[0] == data[0]        # 项完整，数字没被拦腰斩
    assert "已截断" in out and "完整保留" in out


def test_truncate_dict_keeps_pairs_intact():
    data = {f"k{i}": {"value": 3.14159 * i} for i in range(40)}
    text = json.dumps(data, ensure_ascii=False)
    out = truncate_json_safe(text, 250)
    body = out.split("\n…")[0]
    kept = json.loads(body)
    assert 0 < len(kept) < 40
    assert "丢弃字段" in out


def test_truncate_non_json_fallback():
    text = "纯" * 500
    out = truncate_json_safe(text, 100)
    assert out.startswith("纯" * 100)
    assert "已截断" in out


def test_truncate_huge_single_item_fallback():
    text = json.dumps([{"blob": "x" * 1000}])
    out = truncate_json_safe(text, 100)
    assert "已截断" in out  # 单项装不下 → 退回字符硬截，不抛异常


# ──────────────────── M2: session 持久化 ────────────────────

def test_session_persist_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx, "_PERSIST_DIR", tmp_path)
    store = SessionStore()
    sess = store.create()
    sess.messages = [{"role": "system", "content": "s"},
                     {"role": "user", "content": "600519 怎么样"}]
    sess.allowed_tools = {"get_positions", "place_order"}
    sess.turn_start_idx = 1
    store.save(sess)

    fresh = SessionStore()  # 模拟进程重启：全新内存池
    revived = fresh.get(sess.session_id)
    assert revived is not None
    assert revived.messages == sess.messages
    assert revived.allowed_tools == sess.allowed_tools
    assert revived.turn_start_idx == 1
    assert revived.pending_tool_call is None  # 待确认操作绝不跨重启复活


def test_session_persist_rejects_bad_sid(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx, "_PERSIST_DIR", tmp_path)
    store = SessionStore()
    # 用户可控 session_id 不合法格式 → 不读盘、不拼路径
    assert store.get("../../etc/passwd") is None
    assert store.get("mb_XYZ") is None
    evil = AgentSession(session_id="../evil")
    store.save(evil)
    assert not (tmp_path.parent / "evil.json").exists()
    assert list(tmp_path.iterdir()) == []


def test_session_persist_off(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx, "_PERSIST_DIR", tmp_path)
    monkeypatch.setenv("AGENT_SESSION_PERSIST", "off")
    store = SessionStore()
    sess = store.create()
    store.save(sess)
    assert list(tmp_path.iterdir()) == []


# ──────────────────── N2: verdict hooks ────────────────────

def test_verdict_hooks_registered():
    import agents.tools.signal_tools  # noqa: F401 — import 即注册
    import agents.tools.screener_tools  # noqa: F401
    from agents.turn_monitor import VERDICT_HOOKS, classify

    assert "get_today_signals" in VERDICT_HOOKS
    assert "screen_stocks" in VERDICT_HOOKS

    empty_signals = json.dumps({
        "signal_date": "2026-07-03", "is_today": True, "buy_count": 0,
        "sell_count": 0, "returned": 0, "signals": [], "note": "最近交易日无信号"})
    assert classify("get_today_signals", {"ok": True, "summary": empty_signals}) == "ok"

    empty_screen = json.dumps({
        "count": 0, "conditions": ["roe > 20"], "pool_id": None,
        "affordable_only": False, "stocks": [], "note": "没有符合全部条件的股票"})
    assert classify("screen_stocks", {"ok": True, "summary": empty_screen}) == "ok"

    # 没注册钩子的工具维持通用启发式：空列表仍判 empty
    assert classify("some_other_tool", {"ok": True, "summary": '{"signals": []}'}) == "empty"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
