"""提案后验 —— 拿真实盈亏给 AI 的断言打分（S4）。

⭐ 这是整个计划最漂亮的一环：提案带「预期收益/预期胜率/多少天内兑现」，
这里把断言真的拿去证伪 —— **直接拿账户余额给 AI 打分**。

三个必须防的自欺（每条都有单独会红的用例）：

1. 🔴 **「没上线」不是「没达到」** —— 提案被批准 ≠ 新版本被 arm（S2 刻意分开）。
   判成 miss 的话，AI 的战绩会被一堆「Jason 压根没让它跑」拉低。
2. 🔴 **容差不能是 1.0** —— 「预期 +8%、实际 +7.99%」判 miss 是在惩罚精确度。
3. 🔴 **不报「AI 提案胜率 X%」** —— 一年 12-24 条提案，而统计显著要 ≥30 样本。
"""
from datetime import datetime, timedelta

import pytest

from crypto_strategy import proposal_outcome as po
from crypto_strategy import proposals as pr
from crypto_strategy.service import crypto_strategy_service as svc
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    CryptoArenaSwitch,
    CryptoFill,
    CryptoPendingOrder,
    CryptoStrategy,
    CryptoStrategyProposal,
    CryptoStrategyRun,
    CryptoTrade,
)

_SPEC = {
    "name": "测试策略",
    "universe": {"symbols": ["BTCUSDT.BN"]},
    "entry_rules": {"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
    "exit_rules": {"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}},
    "guardrails": {"per_order_notional_usdt": 100, "max_orders_per_day": 3},
}


@pytest.fixture
def clean():
    yield
    s = get_session()
    try:
        for m in (CryptoStrategyRun, CryptoPendingOrder, CryptoStrategyProposal,
                  CryptoArenaSwitch, CryptoTrade, CryptoFill, CryptoStrategy):
            s.query(m).delete()
        s.commit()
    finally:
        s.close()


# ── 1. 纯判定 ───────────────────────────────────────────────────────────

def _j(**kw):
    base = dict(expected_return_pct=0.08, expected_win_rate=0.55,
                actual_return_pct=0.08, actual_win_rate=0.55, window_closed=True)
    return po.judge(**{**base, **kw})


def test_window_not_closed_is_pending():
    assert _j(window_closed=False)["outcome"] == po.PENDING


def test_both_metrics_must_clear():
    """⚠️ 两个指标都要达标。只看收益的话，「押对一把大的、其余全亏」会被记成成功，
    而提案里那句「预期胜率 55%」就白写了。"""
    assert _j()["outcome"] == po.HIT
    assert _j(actual_win_rate=0.1)["outcome"] == po.MISS
    assert _j(actual_return_pct=-0.5)["outcome"] == po.MISS


def test_tolerance_is_not_one():
    """🔴 「预期 +8%、实际 +7%」判 miss 是在惩罚精确度而不是判断力。"""
    r = _j(actual_return_pct=0.07, actual_win_rate=0.50)
    assert r["outcome"] == po.HIT           # 拿到八成算兑现
    assert r["tolerance"] == 0.8
    assert _j(actual_return_pct=0.02, actual_win_rate=0.50)["outcome"] == po.MISS


def test_tolerance_is_configurable():
    assert _j(actual_return_pct=0.07, tolerance=1.0)["outcome"] == po.MISS


def test_no_realized_data_is_unable_not_miss():
    r = _j(actual_return_pct=None, actual_win_rate=None)
    assert r["outcome"] == po.UNABLE
    assert r["unable_reason"] == "no_realized_data"
    assert "无从证伪" in r["reason"]


def test_win_rate_of_no_legs_is_none_not_zero():
    """返 0 的话，「一次都没平仓」和「平了 10 次全亏」在下游看起来一模一样。"""
    assert po._win_rate([]) is None
    assert po._win_rate([1.0, -1.0]) == pytest.approx(0.5)


# ── 2. 端到端 ───────────────────────────────────────────────────────────

def _mk_strategy():
    from crypto_intel_engine.dsl import CryptoStrategySpec
    return svc.compile_and_persist(CryptoStrategySpec(**_SPEC), do_backtest=False)


def _mk_run(strategy_id, minutes_ago=10):
    """给这个版本造一条运行记录 —— 「它到底跑过没有」的判据。"""
    s = get_session()
    try:
        s.add(CryptoStrategyRun(
            strategy_id=strategy_id, status="evaluated", mode="live",
            started_at=datetime.utcnow() - timedelta(minutes=minutes_ago)))
        s.commit()
    finally:
        s.close()


def _mk_proposal(base, **over):
    from crypto_intel_engine.dsl import CryptoStrategySpec
    new = CryptoStrategySpec(**{**_SPEC, "interval_minutes": 15}).model_dump()
    kw = dict(base_strategy_id=base["strategy_id"],
              family_id=base["summary"]["family_id"],
              shortfall="tick 太慢", change_summary="30→15", rationale="短波段走得快",
              expected_return_pct=0.08, expected_win_rate=0.55, horizon_days=30,
              new_spec=new, diff=[{"path": "interval_minutes"}])
    kw.update(over)
    return pr.create_proposal(**kw)


def test_never_armed_proposal_is_unable_not_miss(clean):
    """🔴 最要紧的一条：从没上线的提案**没有可证伪的对象**。

    判成 miss = 用「Jason 压根没让它跑」这个信号去训练 AI —— 那不是 AI 的错。
    """
    base = _mk_strategy()
    p = _mk_proposal(base)                       # 状态是 proposed，没 apply
    r = po.evaluate_proposal(p["proposal_id"])
    assert r["outcome"] == po.UNABLE
    assert r["unable_reason"] == "never_armed"
    assert "不算 AI 判断失误" in r["reason"]


def test_applied_but_new_version_never_ran_is_also_never_armed(clean):
    """🔴 **apply ≠ arm**：批准了提案却从没让新版本上线，同样没有可证伪的对象。

    光看 `status`（此时已经是 applied）分不出这一种。不单独判的话它会掉进
    `no_realized_data`，理由写成「窗口内没有可算的平仓」——
    **把「你没让它跑」说成了「它没跑出成绩」**，而后者是在冤枉 AI。
    """
    base = _mk_strategy()
    p = _mk_proposal(base, horizon_days=1)
    pr.mark_decided(p["proposal_id"], status="applied",
                    applied_strategy_id=base["strategy_id"])
    # 刻意不造运行记录
    r = po.evaluate_proposal(p["proposal_id"], now=datetime.utcnow() + timedelta(days=3))
    assert r["outcome"] == po.UNABLE
    assert r["unable_reason"] == "never_armed"
    assert "没让它跑" in r["reason"]


def test_applied_but_window_open_is_pending(clean):
    base = _mk_strategy()
    p = _mk_proposal(base)
    pr.mark_decided(p["proposal_id"], status="applied",
                    applied_strategy_id=base["strategy_id"])
    _mk_run(base["strategy_id"])          # 它真的跑过（否则判 never_armed）
    r = po.evaluate_proposal(p["proposal_id"])
    assert r["outcome"] == po.PENDING
    assert r["window"]["closed"] is False


def test_applied_with_no_trades_is_unable(clean):
    """跑过、窗口也走完了，但一笔平仓都没有 → 无从证伪，不是「没达到」。"""
    base = _mk_strategy()
    p = _mk_proposal(base, horizon_days=1)
    pr.mark_decided(p["proposal_id"], status="applied",
                    applied_strategy_id=base["strategy_id"])
    _mk_run(base["strategy_id"])          # 它真的跑过（否则判 never_armed）
    r = po.evaluate_proposal(p["proposal_id"], now=datetime.utcnow() + timedelta(days=3))
    assert r["outcome"] == po.UNABLE
    assert r["unable_reason"] == "no_realized_data"


def test_backfill_writes_back_and_skips_terminal_unable(clean):
    """不可重试的 unable 不再重扫 —— 等到天荒地老也不会变。"""
    base = _mk_strategy()
    p = _mk_proposal(base)
    pr.mark_decided(p["proposal_id"], status="applied",
                    applied_strategy_id=base["strategy_id"])
    _mk_run(base["strategy_id"])          # 它真的跑过（否则判 never_armed）
    r1 = po.backfill_outcomes()
    assert r1["scanned"] == 1
    assert pr.get_proposal(p["proposal_id"])["outcome"] == po.PENDING

    # pending 是可重试的，还会被捞
    assert po.backfill_outcomes()["scanned"] == 1

    s = get_session()
    try:
        row = s.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.proposal_id == p["proposal_id"]).first()
        row.outcome, row.unable_reason = po.UNABLE, "never_armed"
        s.commit()
    finally:
        s.close()
    assert po.backfill_outcomes()["scanned"] == 0, "不可重试的 unable 又被重扫了"


def test_outcome_shows_up_in_list_proposals(clean):
    """AI 得看得见自己旧提案兑现没，否则「回头看」这条线还是断的。"""
    base = _mk_strategy()
    p = _mk_proposal(base)
    pr.mark_decided(p["proposal_id"], status="applied",
                    applied_strategy_id=base["strategy_id"])
    _mk_run(base["strategy_id"])          # 它真的跑过（否则判 never_armed）
    po.backfill_outcomes()
    row = pr.list_proposals()[0]
    assert row["outcome"] == po.PENDING
    assert "actual_return_pct" in row and "evaluated_at" in row


def _paper_run(sid, action, qty, price, days_ago):
    """给策略造一条 paper 的「本应成交」决策（`_paper_legs` 认这个形状）。"""
    import json
    s = get_session()
    try:
        s.add(CryptoStrategyRun(
            strategy_id=sid, status="evaluated", mode="paper",
            decision_detail=json.dumps({"decisions": [
                {"symbol": "BTCUSDT.BN", "status": "ready", "action": action,
                 "qty": qty, "price": price}]}),
            started_at=datetime.utcnow() - timedelta(days=days_ago)))
        s.commit()
    finally:
        s.close()


def test_window_is_the_proposals_period_not_the_last_n_days(clean):
    """🔴🔴 回归：一份 60 天前批准、兑现窗口 30 天的提案，要看的是 `[T-60, T-30]`。

    `daily_returns` 的默认窗口**贴着「现在」**往回数 —— 只传 `days=30` 会取到
    `[T-30, T]`，**完全错误的时段，而且不会有任何报错**。

    ⚠️ 这条**必须断言 `actual`**，不能只断言 `evaluate_proposal` 自己拼的 window 字典 ——
    后者根本碰不到取数路径（复审实测：去掉 since/until 之后那种写法照样绿）。
    做法：窗口内赚 100、窗口外（更近）赚 9999，只有窗口切对了才拿得到 100。
    """
    base = _mk_strategy()
    sid = base["strategy_id"]
    _paper_run(sid, "BUY", 1.0, 100.0, days_ago=58)      # 窗口内
    _paper_run(sid, "SELL", 1.0, 200.0, days_ago=45)     # 窗口内平仓 → +100（毛）
    _paper_run(sid, "BUY", 1.0, 100.0, days_ago=10)      # 窗口外（更近）
    _paper_run(sid, "SELL", 1.0, 10099.0, days_ago=5)    # 窗口外平仓 → +9999

    p = _mk_proposal(base, horizon_days=30)
    pr.mark_decided(p["proposal_id"], status="applied", applied_strategy_id=sid)
    s = get_session()
    try:
        row = s.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.proposal_id == p["proposal_id"]).first()
        row.decided_at = datetime.utcnow() - timedelta(days=60)
        s.commit()
    finally:
        s.close()

    r = po.evaluate_proposal(p["proposal_id"])
    assert r["window"]["closed"] is True
    # 🔑 真闸门：窗口内那笔 +100（扣两腿手续费）应该在，窗口外那笔 +9999 不该在。
    got = r["actual"]["return_pct"]
    assert 0 < got < 0.5, f"窗口切错了，取到了窗口外的收益：{got}"
    assert r["actual"]["closed_legs"] == 1


def test_window_end_does_not_stick_to_now(clean):
    """窗口末端必须停在 `decided_at + horizon`，不是「现在」。"""
    from common.market_time import utc_now
    base = _mk_strategy()
    sid = base["strategy_id"]
    p = _mk_proposal(base, horizon_days=30)
    pr.mark_decided(p["proposal_id"], status="applied", applied_strategy_id=sid)
    _mk_run(sid)
    s = get_session()
    try:
        row = s.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.proposal_id == p["proposal_id"]).first()
        row.decided_at = datetime.utcnow() - timedelta(days=60)
        s.commit()
    finally:
        s.close()

    w = po.evaluate_proposal(p["proposal_id"])["window"]
    since = po._decided_at({"decided_at": w["since"]})
    until = po._decided_at({"decided_at": w["until"]})
    assert (until - since).days == 30
    assert (utc_now() - until).days >= 29, "窗口贴到「现在」去了"


# ── 3. 战绩总账：**不许报胜率** ──────────────────────────────────────────

def test_scorecard_never_reports_a_win_rate(clean):
    """🔴 一年 12-24 条提案，统计显著要 ≥30 样本 —— 报百分比等于给噪声盖权威章。"""
    base = _mk_strategy()
    for _ in range(3):
        _mk_proposal(base)
    card = po.scorecard()
    assert card["proposals"] == 3
    assert "win_rate" not in card
    assert "hit_rate" not in card
    assert "样本不足以下任何结论" in card["note"]


def test_scorecard_tool_line_has_no_percentage(clean):
    from agents.tools.crypto_strategy_tools import get_proposal_scorecard
    base = _mk_strategy()
    _mk_proposal(base)
    r = get_proposal_scorecard(days=365)
    assert "%" not in r.message
    assert "样本" in r.message or "太早" in r.message


def _decided(pid, outcome, basis="live_fills"):
    s = get_session()
    try:
        row = s.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.proposal_id == pid).first()
        row.outcome, row.outcome_basis = outcome, basis
        s.commit()
    finally:
        s.close()


def test_scorecard_line_never_formats_a_rate(clean):
    """🔴 覆盖**会泄漏百分比的那条分支**（hit+miss>0）。

    此前只有 pending 的用例，于是唯一可能泄漏的那一行根本没被跑到 ——
    复审把它换成 `f"兑现率 {hit/(hit+miss):.0%}"`，全套照过。
    """
    from agents.tools.crypto_strategy_tools import get_proposal_scorecard
    base = _mk_strategy()
    a, b = _mk_proposal(base), _mk_proposal(base, change_summary="另一个改法")
    _decided(a["proposal_id"], po.HIT)
    _decided(b["proposal_id"], po.MISS)
    r = get_proposal_scorecard(days=365, refresh=False)
    assert "%" not in r.message, f"泄漏了百分比：{r.message}"
    assert "兑现率" not in r.message and "胜率" not in r.message


def test_scorecard_separates_paper_evidence_from_real_money(clean):
    """🔴 纸面跑出来的 hit **不等于**真钱兑现 —— 合并计数就是拿模拟成绩背书。"""
    base = _mk_strategy()
    a, b = _mk_proposal(base), _mk_proposal(base, change_summary="另一个改法")
    _decided(a["proposal_id"], po.HIT, basis="live_fills")
    _decided(b["proposal_id"], po.HIT, basis="paper_simulated")
    card = po.scorecard()
    assert card["by_outcome"]["hit"] == 2
    assert card["by_outcome_and_basis"] == {"hit:live_fills": 1,
                                            "hit:paper_simulated": 1}
    assert card["decided_with_real_money"] == 1
    assert "真金白银 1 条" in card["note"]


def test_outcome_basis_is_persisted(clean):
    """`basis` 不落库的话，库里/工具返回里都分不出这条 hit 是不是模拟的。"""
    base = _mk_strategy()
    sid = base["strategy_id"]
    p = _mk_proposal(base, horizon_days=1)
    pr.mark_decided(p["proposal_id"], status="applied", applied_strategy_id=sid)
    _paper_run(sid, "BUY", 1.0, 100.0, days_ago=0)
    _paper_run(sid, "SELL", 1.0, 120.0, days_ago=0)
    po.backfill_outcomes()
    row = pr.get_proposal(p["proposal_id"])
    assert row["outcome_basis"] == "paper_simulated"


def test_freshly_applied_is_pending_not_permanently_never_armed(clean):
    """🔴🔴 blocker 回归：`apply` 生成的是草稿，run 由引擎按 tick 写（默认 30 分钟）。

    所以「applied 但一条 run 都没有」是**每一份提案的必经常态**。窗口没走完就把它
    写成终态 `never_armed`，回填再也不会重扫 —— **最新的提案反而最容易被永久误销号**，
    正好把「拿账户余额给 AI 打分」打成反面。
    """
    base = _mk_strategy()
    sid = base["strategy_id"]
    p = _mk_proposal(base, horizon_days=30)
    pr.mark_decided(p["proposal_id"], status="applied", applied_strategy_id=sid)

    r = po.evaluate_proposal(p["proposal_id"])       # 还没 tick 过
    assert r["outcome"] == po.PENDING
    assert "还没跑起来" in r["reason"]

    po.backfill_outcomes()
    # 之后引擎跑起来了，仍然捞得回来（pending 是可重试的）
    _mk_run(sid)
    assert po.backfill_outcomes()["scanned"] == 1


def test_negative_expectation_tolerance_does_not_invert(clean):
    """容差的语义是「差一点也算兑现」，负预期时也得往**更宽容**的方向挪。"""
    r = po.judge(expected_return_pct=-0.02, expected_win_rate=0.4,
                 actual_return_pct=-0.018, actual_win_rate=0.4,
                 window_closed=True)
    assert r["outcome"] == po.HIT, "比承诺的还好，却被判 miss"
    assert r["return_bar"] < -0.02


def test_scorecard_negative_when_empty(clean):
    from agents.tools.crypto_strategy_tools import get_proposal_scorecard
    r = get_proposal_scorecard()
    assert r.business_result == "negative"


# ── 4. 双重留痕收口（S4 的另一半）────────────────────────────────────────

def test_log_decision_switch_suppresses_the_duplicate_row(clean):
    """🔴 同一张单不该留两条 DecisionLog。

    走 confirm_gate 的路径由确认门记（信息更全），执行层就别再记一条。
    """
    from crypto_intel_engine.execution import record_crypto_resting, record_crypto_trade
    from data_engine.storage.models import DecisionLog

    def _count():
        s = get_session()
        try:
            return s.query(DecisionLog).filter(DecisionLog.source == "crypto").count()
        finally:
            s.close()

    before = _count()
    note1 = record_crypto_trade("BTCUSDT.BN", "BUY", 65000.0, 0.001, "oid-x",
                                source_kind="ai_advice", log_decision=False)
    note2 = record_crypto_resting("ETHUSDT.BN", "BUY", 1900.0, "oid-y",
                                  log_decision=False)
    assert _count() == before, "关掉 log_decision 之后还在写 DecisionLog"

    # ⚠️ 但那句人话必须被返回，让调用方带进 exec_note —— 否则收口就是拿可读性换整洁
    assert "币安现货成交" in note1 and "oid-x" in note1
    assert "挂出" in note2 and "oid-y" in note2

    # 默认仍然写（`pending.py` 不走 confirm_gate，关掉就是留痕断线）
    record_crypto_trade("BTCUSDT.BN", "SELL", 66000.0, 0.001, "oid-z",
                        source_kind="strategy", source_ref="CS-1")
    assert _count() == before + 1
    s = get_session()
    try:
        s.query(DecisionLog).filter(DecisionLog.source == "crypto").delete()
        s.query(CryptoTrade).delete()
        s.commit()
    finally:
        s.close()


def test_place_crypto_order_carries_exec_note():
    """收口之后那句人话仍要出现在工具返回里（confirm_gate 记 output_summary 时带走）。

    ⚠️ 断言**只比数字，绝不把整份源码写进 assert 表达式**：pytest 的断言反省会为
    失败信息构造那个 30KB 字符串的 repr —— 实测让这一条测试跑了 16 分钟。
    """
    import ast
    import inspect

    from agents.tools import crypto_tools

    tree = ast.parse(inspect.getsource(crypto_tools))
    off, notes = 0, 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "log_decision" and getattr(kw.value, "value", None) is False:
                    off += 1
        elif isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value == "exec_note":
                    notes += 1
    assert off == 2, f"两条留痕路径都要关，实际关了 {off} 条"
    assert notes == 2, f"两条路径都要把人话带进返回，实际 {notes} 条"
