"""
TurnMonitor 函数级直测（pytest 可跑，也可 conda run -n quant python tests/test_turn_monitor.py 直跑）。
"""
import json
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.turn_monitor import (  # noqa: E402
    TurnMonitor, _args_hash, classify, verdict_hook, VERDICT_HOOKS,
    _COMPACT_MARK,
)


def _td(confirm=False, subagent=False):
    return SimpleNamespace(requires_confirmation=confirm, is_subagent=subagent)


def _tc(name, args, cid="c1"):
    return {"id": cid, "name": name, "args": args}


def _session(messages=None):
    return SimpleNamespace(messages=messages if messages is not None else [])


# ──────────────────── _args_hash ────────────────────

def test_args_hash():
    assert _args_hash("t", {"a": 1, "b": 2}) == _args_hash("t", {"b": 2, "a": 1})
    assert _args_hash("t", {"a": 1}) == _args_hash("t", {"a": 1, "_confirmed": True})
    assert _args_hash("t", {"a": 1}) != _args_hash("t", {"a": 2})
    assert _args_hash("t1", {}) != _args_hash("t2", {})
    assert _args_hash("t", None) == _args_hash("t", {})


# ──────────────────── classify ────────────────────

def test_classify_matrix():
    assert classify("x", {"ok": False, "summary": "报错了"}) == "error"
    assert classify("x", {"ok": True, "summary": ""}) == "empty"
    assert classify("x", {"ok": True, "summary": "暂无数据"}) == "empty"
    assert classify("x", {"ok": True, "summary": "未找到该股票"}) == "empty"
    assert classify("x", {"ok": True, "summary": "[]"}) == "empty"
    assert classify("x", {"ok": True, "summary": "{}"}) == "empty"
    assert classify("x", {"ok": True, "summary": '{"items": []}'}) == "empty"
    assert classify("x", {"ok": True, "summary": '{"data": null, "results": []}'}) == "empty"
    # 正常短答案不误判（无空词）
    assert classify("x", {"ok": True, "summary": "是，该股已在自选中"}) == "ok"
    # 长文本即使含「暂无」也不判空（len>80 门槛）
    long_text = "贵州茅台今日上涨2.3%，" * 10 + "板块资金暂无明显流出。"
    assert classify("x", {"ok": True, "summary": long_text}) == "ok"
    # 正常 JSON 数据
    assert classify("x", {"ok": True, "summary": '{"items": [{"s": "AAPL"}]}'}) == "ok"
    # summary 非 str（executor 兜底前的原始 dict）也能处理
    assert classify("x", {"ok": True, "summary": {"price": 182.5}}) == "ok"
    # code-review 发现并修复：ToolEnvelope 迁移后的 business_result="negative"
    # （比如 place_order 风控拒单）必须直接判定，不能靠 summary 字符串里的
    # key 名启发式猜——拒单 payload 是 {executed,reason,failed_rules}，不在
    # 旧版检查的 key 白名单里，会被误判成 "ok"。business_result 判定优先于
    # 字符串启发式。
    #
    # 第二轮 code-review 又发现：negative 曾经被归到 "empty"（在 _BAD_VERDICTS
    # 里），导致「风控正确拒单」这种诚实的终态否定被当成连续坏调用累加，3 次
    # 后触发「停止重试」nudge——但风控拒单不是需要重试的失败。现在给它独立的
    # "negative" verdict，且不计入 _BAD_VERDICTS（见 test_negative_not_bad_streak）。
    assert classify("place_order", {
        "ok": True, "business_result": "negative",
        "summary": '{"executed": false, "reason": "风控未通过", "failed_rules": ["x"]}',
    }) == "negative"
    assert classify("place_order", {
        "ok": True, "business_result": "affirmative",
        "summary": '{"executed": true, "symbol": "600519.SH"}',
    }) == "ok"


def test_verdict_hook_override():
    @verdict_hook("my_special_tool")
    def _hook(s):
        return "ok" if "今日无信号" in s else None
    try:
        assert classify("my_special_tool", {"ok": True, "summary": "今日无信号"}) == "ok"
        # 钩子返回 None 走通用启发式
        assert classify("my_special_tool", {"ok": True, "summary": "暂无数据"}) == "empty"
    finally:
        VERDICT_HOOKS.pop("my_special_tool", None)


# ──────────────────── 重复拦截 ────────────────────

def test_duplicate_block():
    mon = TurnMonitor(turn_start_idx=0)
    tc = _tc("get_kline", {"symbol": "600519"})
    assert mon.check_duplicate(tc, _td()) is None  # 首跑放行
    mon.record_result(tc, _td(), {"ok": True, "summary": "60 根日线，最新收盘 1700"},
                      elapsed_ms=10, msg_index=0)
    blocked = mon.check_duplicate(tc, _td())
    assert blocked and "60 根日线" in blocked  # 二跑拦截并回放摘录
    # 不同参数放行
    assert mon.check_duplicate(_tc("get_kline", {"symbol": "000001"}), _td()) is None


def test_duplicate_exemptions():
    mon = TurnMonitor(turn_start_idx=0)
    # 确认流工具整体豁免
    tc = _tc("place_order", {"symbol": "600519", "side": "buy"})
    mon.record_result(tc, _td(confirm=True), {"ok": True, "summary": "已下单"},
                      elapsed_ms=5, msg_index=0)
    assert mon.check_duplicate(tc, _td(confirm=True)) is None
    # 实时类工具超 TTL 放行
    os.environ["AGENT_TM_DUP_TTL_S"] = "1"
    try:
        tc2 = _tc("get_positions", {})
        rec = mon.record_result(tc2, _td(), {"ok": True, "summary": "持仓 3 只"},
                                elapsed_ms=5, msg_index=1)
        assert mon.check_duplicate(tc2, _td()) is not None  # TTL 内拦
        rec.executed_at = time.time() - 5
        assert mon.check_duplicate(tc2, _td()) is None      # 超 TTL 放行
    finally:
        del os.environ["AGENT_TM_DUP_TTL_S"]


def test_duplicate_error_retry_once():
    mon = TurnMonitor(turn_start_idx=0)
    tc = _tc("get_news", {"symbol": "600519"})
    mon.record_result(tc, _td(), {"ok": False, "summary": "网络超时"},
                      elapsed_ms=5, msg_index=0)
    assert mon.check_duplicate(tc, _td()) is None  # 首次失败允许重试一次
    mon.record_result(tc, _td(), {"ok": False, "summary": "网络超时"},
                      elapsed_ms=5, msg_index=1)
    assert mon.check_duplicate(tc, _td()) is not None  # 第 3 次相同调用拦


def test_seen_registers_most_recent_call_not_first():
    """code-review 发现并修复的 bug：self.seen 曾经用 setdefault 写入，永远
    停在第一次记录，TTL 判断因此比对一个不会推进的旧时间戳，实时类工具 TTL
    到期后会永久放行、拦截失效。修复后 self.seen 必须是"最近一次"记录。"""
    mon = TurnMonitor(turn_start_idx=0)
    tc = _tc("get_positions", {})
    h = _args_hash("get_positions", {})

    rec1 = mon.record_result(tc, _td(), {"ok": True, "summary": "持仓 3 只"},
                             elapsed_ms=5, msg_index=0)
    assert mon.seen[h] is rec1

    # 模拟 TTL 已过（不改真实时间，直接推早 rec1 的时间戳，同 test_duplicate_exemptions 手法）
    os.environ["AGENT_TM_DUP_TTL_S"] = "1"
    try:
        rec1.executed_at = time.time() - 5
        assert mon.check_duplicate(tc, _td()) is None  # TTL 已过，放行刷新

        # 真正刷新执行一次，记录第二条结果
        rec2 = mon.record_result(tc, _td(), {"ok": True, "summary": "持仓 5 只"},
                                 elapsed_ms=5, msg_index=1)
        # 核心断言：self.seen 必须更新成最近一次记录，而不是仍停在 rec1
        assert mon.seen[h] is rec2
        assert mon.seen[h].summary_head == "持仓 5 只"

        # 刷新完立刻再调一次：应该在新的 TTL 窗口内被拦截（不是永久放行）
        blocked = mon.check_duplicate(tc, _td())
        assert blocked is not None and "持仓 5 只" in blocked
    finally:
        del os.environ["AGENT_TM_DUP_TTL_S"]


# ──────────────────── streak / 软硬线状态机 ────────────────────

def test_negative_not_bad_streak():
    """风控正确拒单（business_result="negative"）是诚实的终态否定，不是失败，
    不该像 "empty"/"error"/"duplicate" 一样累加 consecutive_bad、触发 L2 nudge。"""
    mon = TurnMonitor(turn_start_idx=0)
    sess = _session()
    for i in range(5):
        mon.record_result(
            _tc("place_order", {"i": i}, cid=f"c{i}"), _td(),
            {"ok": True, "business_result": "negative",
             "summary": '{"executed": false, "reason": "风控未通过"}'},
            elapsed_ms=1, msg_index=i)
    assert mon.consecutive_bad == 0
    events, stop = mon.pre_round(sess)
    assert not stop and not sess.messages  # 不该弹出「连续失败」nudge


def test_bad_streak_nudge():
    mon = TurnMonitor(turn_start_idx=0)
    sess = _session()
    for i in range(3):
        mon.record_result(_tc("t", {"i": i}, cid=f"c{i}"),
                          _td(), {"ok": True, "summary": "暂无数据"},
                          elapsed_ms=1, msg_index=i)
    events, stop = mon.pre_round(sess)
    assert not stop
    assert len(sess.messages) == 1 and "连续失败/空结果/重复" in sess.messages[0]["content"]
    # 同一 streak 值不重复发
    events2, _ = mon.pre_round(sess)
    assert len(sess.messages) == 1 and not events2
    # streak 到 6 升级再发一次
    for i in range(3, 6):
        mon.record_result(_tc("t", {"i": i}, cid=f"c{i}"),
                          _td(), {"ok": True, "summary": "暂无数据"},
                          elapsed_ms=1, msg_index=i)
    mon.pre_round(sess)
    assert len(sess.messages) == 2
    # 成功一次清零
    mon.record_result(_tc("t", {"i": 99}), _td(),
                      {"ok": True, "summary": "拿到 20 条数据，明细如下……"},
                      elapsed_ms=1, msg_index=6)
    assert mon.consecutive_bad == 0 and mon.nudged_at_streak == 0


def test_soft_and_hard_budget():
    mon = TurnMonitor(turn_start_idx=0)
    sess = _session()
    mon.record_llm_round(30_000, 1_000)
    events, stop = mon.pre_round(sess)
    assert not stop and not sess.messages  # 未到软线
    mon.record_llm_round(20_000, 1_000)    # 累计 52k
    events, stop = mon.pre_round(sess)
    assert not stop and len(sess.messages) == 1 and "收敛" in sess.messages[0]["content"]
    events, stop = mon.pre_round(sess)
    assert len(sess.messages) == 1  # 软线只警一次
    mon.record_llm_round(70_000, 0)        # 累计 122k
    events, stop = mon.pre_round(sess)
    assert stop and mon.hard_tripped       # 硬熔断


def test_intervene_off_switch():
    os.environ["AGENT_TM_INTERVENE"] = "off"
    try:
        mon = TurnMonitor(turn_start_idx=0)
        sess = _session()
        tc = _tc("get_kline", {"symbol": "600519"})
        mon.record_result(tc, _td(), {"ok": True, "summary": "数据"},
                          elapsed_ms=1, msg_index=0)
        assert mon.check_duplicate(tc, _td()) is None  # 不拦
        mon.record_llm_round(999_999, 0)
        events, stop = mon.pre_round(sess)
        assert not stop and not events and not sess.messages  # 不干预
    finally:
        del os.environ["AGENT_TM_INTERVENE"]


# ──────────────────── 轮中压缩 ────────────────────

def _build_turn(mon, n_calls, content_len=1000):
    """构造 n 个 assistant(tool_calls)+tool 配对消息并登记 record。"""
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "问题"}]
    for i in range(n_calls):
        cid = f"call_{i}"
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": cid, "type": "function",
                                     "function": {"name": "t", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": cid, "content": "x" * content_len})
        mon.record_result(_tc("t", {"i": i}, cid=cid), _td(),
                          {"ok": True, "summary": "x" * content_len},
                          elapsed_ms=1, msg_index=len(msgs) - 1)
    return msgs


def test_compact_turn_inplace():
    mon = TurnMonitor(turn_start_idx=1)
    msgs = _build_turn(mon, 7)
    sess = _session(msgs)
    before_roles = [(m.get("role"), m.get("tool_call_id")) for m in msgs]
    seen_before = set(mon.seen)

    n, saved = mon._compact_turn_inplace(sess, keep_recent=4, max_chars=400)
    assert n == 3 and saved > 0  # 7 条中保最近 4 条，压前 3 条
    # 角色序列与 tool_call_id 配对完全不变
    assert [(m.get("role"), m.get("tool_call_id")) for m in msgs] == before_roles
    # 前 3 条被截且带标记，后 4 条原样
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    for m in tool_msgs[:3]:
        assert m["content"].endswith(_COMPACT_MARK) and len(m["content"]) < 500
    for m in tool_msgs[3:]:
        assert len(m["content"]) == 1000
    # 被压缩的记录移出 seen（允许模型重取全量）
    assert len(mon.seen) == len(seen_before) - 3
    # 幂等：二压不再截
    n2, _ = mon._compact_turn_inplace(sess, keep_recent=4, max_chars=400)
    assert n2 == 0


def test_compact_defends_index_drift():
    """下标越界/漂移到非 tool 消息时跳过，绝不误伤。"""
    mon = TurnMonitor(turn_start_idx=0)
    mon.record_result(_tc("t", {"a": 1}), _td(),
                      {"ok": True, "summary": "x" * 1000}, elapsed_ms=1, msg_index=99)
    mon.record_result(_tc("t", {"a": 2}), _td(),
                      {"ok": True, "summary": "x" * 1000}, elapsed_ms=1, msg_index=0)
    sess = _session([{"role": "user", "content": "y" * 1000}])
    n, _ = mon._compact_turn_inplace(sess, keep_recent=0, max_chars=400)
    assert n == 0 and sess.messages[0]["content"] == "y" * 1000


# ──────────────────── 汇总 ────────────────────

def test_summary_fields():
    mon = TurnMonitor(turn_start_idx=0)
    mon.record_llm_round(1000, 100)
    mon.record_result(_tc("a", {}), _td(), {"ok": True, "summary": "好数据" * 30},
                      elapsed_ms=120, msg_index=0)
    mon.record_result(_tc("b", {}), _td(subagent=True), {"ok": True, "summary": ""},
                      elapsed_ms=5000, msg_index=1)
    mon.record_duplicate(_tc("a", {}))
    s = mon.summary_fields()
    assert s["rounds"] == 1 and s["calls"] == 3
    assert s["verdicts"] == {"ok": 1, "empty": 1, "duplicate": 1}
    assert s["elapsed_ms_total"] == 5120 and s["subagent_calls"] == 1
    assert s["turn_tokens"] == {"prompt": 1000, "completion": 100}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"\n全部 {len(fns)} 个测试通过")
