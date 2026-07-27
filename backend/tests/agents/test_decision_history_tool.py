"""`get_decision_history` 的查询侧可见性（S0 §2.3-2.6）。

这四件事都是「不做就会让 LLM 说出**具体错话**」的：

| 缺口 | LLM 会说的错话 | 真相 |
|---|---|---|
| source 查不了 crypto | 「没有 crypto 记录」 | 建议在 `crypto_cockpit` 里躺着 |
| execution 全是 `executed=false` | 「你有 16 笔未成交订单」 | 真挂在币安的只有 5 张，6 次根本没到交易所 |
| 完整快照永久看不见 | 「看不到当时的输入」 | `/decisions` 页面**不存在**，本工具是唯一出口 |
| entry_kind 一开就能算回执的胜率 | 「crypto 胜率 100%」 | 回执没有对错可评（P0-4 那颗炸弹） |
"""
import pytest

from agents.tools.decision_tools import (
    _derive_exec_state,
    _derive_order_id,
    get_decision_history,
)
from common.decision_kind import ADVICE, EXECUTION, OPS
from data_engine.storage.database import get_session
from data_engine.storage.models import DecisionLog


@pytest.fixture
def seeded():
    """造一小撮留痕：2 条 crypto_cockpit 建议 + 3 类 crypto 回执 + 1 条 ops。"""
    import decision_log

    ids = []
    ids.append(decision_log.record_decision(
        source="crypto_cockpit", entry_kind=ADVICE, symbol="BTCUSDT.BN", action="BUY",
        confidence=72.0, entry_price=65000.0, stop_loss=61750.0,
        input_snapshot={"rsi": 41}, output_text="币版驾驶舱：买入"))
    ids.append(decision_log.record_decision(
        source="crypto_cockpit", entry_kind=ADVICE, symbol="ETHUSDT.BN", action="HOLD",
        confidence=48.0))
    # ① 真挂在币安（confirm_gate 侧的结构化留痕）
    ids.append(decision_log.record_decision(
        source="moneybill", entry_kind=EXECUTION, symbol="ETHUSDT.BN", action="BUY",
        entry_price=1900.0, executed=False,
        output_summary={"executed": False, "resting": True, "order_id": "48545206638",
                        "status": "SUBMITTED", "price": 1900.0}))
    # ② 同一张单的 crypto 侧留痕（双重留痕，S4 才收口 —— 这里只保证计数别撒谎）
    ids.append(decision_log.record_decision(
        source="crypto", entry_kind=EXECUTION, symbol="ETHUSDT.BN", action="BUY",
        entry_price=1900.0, executed=False,
        output_text="币安限价单挂出（未成交，盘口等待撮合） order=48545206638"))
    # ③ 根本没到交易所
    ids.append(decision_log.record_decision(
        source="moneybill", entry_kind=EXECUTION, symbol="SOLUSDT.BN", action="BUY",
        executed=False,
        output_summary={"executed": False, "reason": "金额 4.97 低于最小名义额 5.0"}))
    # ④ 非交易操作
    ids.append(decision_log.record_decision(
        source="moneybill", entry_kind=OPS, symbol="600519.SH", action=None,
        output_summary={"executed": True, "tool": "add_to_watchlist"}))

    yield [i for i in ids if i]

    session = get_session()
    try:
        session.query(DecisionLog).filter(DecisionLog.decision_id.in_(ids)).delete(
            synchronize_session=False)
        session.commit()
    finally:
        session.close()


# ── exec_state 派生（纯函数，先单独钉死判定顺序）─────────────────────────

@pytest.mark.parametrize("row,want", [
    ({"executed": True}, "filled"),
    ({"executed": False, "output_summary": {"resting": True, "price": 1900.0}}, "resting"),
    ({"executed": False, "output_summary": {"reason": "风控未通过"}}, "blocked"),
    # reason 与 resting 同时出现时以 resting 优先：钱已经挂出去了，比「被拦」更该被看见
    ({"executed": False, "output_summary": {"resting": True, "price": 1.0, "reason": "x"}},
     "resting"),
    # 🔴 `state_unknown` 必须压过 `reason`：这是**唯一一个钱可能真动了**的状态，
    # 判成 blocked 等于告诉 LLM「钱没动」。写入侧原文：「这笔单可能已经成交」。
    ({"executed": False,
      "output_summary": {"state_unknown": True, "reason": "请求超时且回查未果"}}, "unknown"),
    # 补录券商 App 成交：回执没有 `executed` 键 → confirm_gate 写成 False，
    # 但它按定义就是既成的成交
    ({"executed": False, "output_summary": {"recorded": True, "amount": 12345.0}}, "filled"),
    # 市价单零成交：写入侧也给了 resting=True（LIMIT/MARKET 不分家），
    # 但 price 明确是 None → 没有限价 → 不算「挂在盘口」
    ({"executed": False,
      "output_summary": {"resting": True, "price": None, "status": "EXPIRED"}}, "unknown"),
    # 反过来：**没有 price 这个键**不等于「没有限价」，别误杀精简回执
    ({"executed": False, "output_summary": {"resting": True}}, "resting"),
    # crypto 侧只有人话，没有结构化字段
    ({"executed": False, "output_text": "币安限价单挂出（未成交） order=1"}, "resting"),
    ({"executed": False, "output_text": "币安现货成交 order=1 0.5 @ $65,000"}, "filled"),
    # 市价单零成交：既没成交也没挂住 —— **别硬塞进三分类里说瞎话**
    ({"executed": False, "output_text": "币安市价单零成交（异常：薄盘/交易对暂停/被拒）"},
     "unknown"),
    ({"executed": False}, "unknown"),
    # output_summary 存成字符串（json.loads 失败时 _to_dict 会原样返回）也认
    ({"executed": False, "output_summary": '{"resting": true}'}, "resting"),
    ({"executed": False, "output_summary": "不是 json"}, "unknown"),
])
def test_derive_exec_state(row, want):
    assert _derive_exec_state(row) == want


def test_derive_order_id_from_both_shapes():
    assert _derive_order_id({"output_summary": {"order_id": "123"}}) == "123"
    assert _derive_order_id({"output_text": "币安限价单挂出 order=48545206638"}) == "48545206638"
    assert _derive_order_id({"output_text": "没有单号"}) is None


# ── 指路：source 里只有回执时不能静默返空 ─────────────────────────────────

def test_execution_only_source_points_to_the_advice_source(seeded):
    r = get_decision_history(source="crypto")          # 默认 entry_kind=advice
    assert r.business_result == "negative"
    msg = r.message
    assert "crypto_cockpit" in msg, f"没指路：{msg}"
    assert "entry_kind" in msg, f"没告诉 LLM 怎么才能看到这些回执：{msg}"


def test_unknown_source_does_not_fabricate_a_claim(seeded):
    """没登记的 source 不许被说成「里面只有既成事实」——那是凭空捏造。

    （LLM 走不到这条路，executor 会用 Literal 拦掉；直接调 Python 走得到。）
    """
    r = get_decision_history(source="没这个来源")
    assert r.business_result == "negative"
    assert "既成事实" not in r.message


def test_hint_text_is_readable_not_python_repr(seeded):
    """指路文案里不许出现 `['execution', 'ops']` 这种 repr —— LLM 会照着传。"""
    msg = get_decision_history(source="crypto").message
    assert "[" not in msg and "'" not in msg
    assert "entry_kind=execution" in msg


def test_advice_source_empty_result_keeps_plain_message(seeded):
    """建议类 source 查空了是普通的「没有记录」，别硬塞指路文案。"""
    r = get_decision_history(source="advisor", symbol="不存在的票")
    assert r.business_result == "negative"
    assert "crypto_cockpit" not in r.message


def test_crypto_advice_is_reachable_by_source(seeded):
    """本卡的起因：MoneyBill 得能按 crypto 来源查胜率。"""
    r = get_decision_history(source="crypto_cockpit")
    assert r.business_result != "negative"
    assert r.data["total"] >= 2
    assert {d["source"] for d in r.data["decisions"]} == {"crypto_cockpit"}


# ── entry_kind 入参：只作用于样本，stats 恒定只算 advice ──────────────────

def test_entry_kind_filters_samples_only(seeded):
    adv = get_decision_history(entry_kind="advice", limit=50)
    exe = get_decision_history(entry_kind="execution", limit=50)

    assert all(d["entry_kind"] == ADVICE for d in adv.data["decisions"])
    assert all(d["entry_kind"] == EXECUTION for d in exe.data["decisions"])
    # ⭐ 关键不变式：换了 entry_kind，胜率口径**一个字都不能变**
    assert adv.data["stats_scope"] == ADVICE
    assert exe.data["stats_scope"] == ADVICE
    assert exe.data["stats"] == adv.data["stats"]
    assert exe.data["calibration"] == adv.data["calibration"]
    assert exe.data["queried_entry_kind"] == "execution"


def test_entry_kind_all_covers_every_kind(seeded):
    r = get_decision_history(entry_kind="all", limit=50)
    kinds = {d["entry_kind"] for d in r.data["decisions"]}
    assert {ADVICE, EXECUTION, OPS} <= kinds


def test_bad_entry_kind_falls_back_to_advice(seeded):
    r = get_decision_history(entry_kind="回执", limit=50)
    assert r.data["queried_entry_kind"] == ADVICE


# ── exec_overview：别让「16 条」被说成「16 张单」──────────────────────────

def test_exec_overview_counts_distinct_orders(seeded):
    r = get_decision_history(entry_kind="execution", limit=50)
    ov = r.data["exec_overview"]
    assert ov["counts"]["resting"] == 2      # 两条留痕
    assert ov["counts"]["blocked"] == 1
    assert ov["distinct_orders"] == 1        # **但只有一张真单**
    assert "distinct_orders" in ov["note"]


def test_exec_overview_does_not_move_with_limit(seeded):
    """🔴 摘要必须是全量口径 —— 随 limit 变的数字比没有还糟。

    这条是审查揪出来的：第一版把摘要建在被 limit 截断的样本上，于是
    「你下过几张单」会随「返回几条样本」变，旁边还摆着一个全量的 total。
    """
    small = get_decision_history(entry_kind="execution", limit=1).data["exec_overview"]
    big = get_decision_history(entry_kind="execution", limit=50).data["exec_overview"]
    assert small == big
    assert small["scope"] == "all_matching"


def test_exec_overview_also_present_under_all(seeded):
    """`entry_kind=all` 的样本里混着回执，摘要照给（且仍是全量口径）。"""
    r = get_decision_history(entry_kind="all", limit=50)
    assert r.data["exec_overview"]["distinct_orders"] == 1


def test_advice_samples_carry_no_exec_state(seeded):
    r = get_decision_history(entry_kind="advice", limit=50)
    assert all("exec_state" not in d for d in r.data["decisions"])
    assert "exec_overview" not in r.data


# ── verbose：完整快照的唯一出口 ───────────────────────────────────────────

def test_verbose_returns_full_snapshot(seeded):
    r = get_decision_history(source="crypto_cockpit", limit=2, verbose=True)
    assert r.data.get("verbose") is True
    d = r.data["decisions"][0]
    assert "input_snapshot" in d and "output_text" in d and "output_summary" in d


def test_verbose_is_ignored_above_limit_cap(seeded):
    """limit 大时 verbose 失效 —— 全量快照回灌会把 token 打爆。

    但**不能静默失效**（TurnMonitor 那条教训）：得明说没生效 + 怎么才能生效，
    否则 LLM 会以为「快照本来就是空的」。
    """
    r = get_decision_history(limit=50, verbose=True)
    assert "verbose" not in r.data
    assert all("input_snapshot" not in d for d in r.data["decisions"])
    assert "verbose 未生效" in r.data["verbose_suppressed"]


def test_no_suppressed_notice_when_verbose_not_asked(seeded):
    assert "verbose_suppressed" not in get_decision_history(limit=50).data


def test_default_is_lean(seeded):
    r = get_decision_history(source="crypto_cockpit", limit=2)
    assert all("input_snapshot" not in d for d in r.data["decisions"])
