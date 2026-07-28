"""策略变更提案 + 确定性 diff（S2）。

**最要紧的一条**：diff 由代码逐字段比出来，不是 AI 自己描述的。
让 AI 说自己改了什么，它可能说漏（改三处只说两处）、说错（说改阈值实际动了冷却时间），
而 Jason 在确认框里要审的正是「到底改了哪几个数」。
"""
import pytest

from crypto_strategy import proposals as pr
from crypto_strategy.service import crypto_strategy_service as svc
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    CryptoArenaSwitch,
    CryptoPendingOrder,
    CryptoStrategy,
    CryptoStrategyProposal,
    CryptoStrategyRun,
)

_SPEC = {
    "name": "测试策略",
    "universe": {"symbols": ["BTCUSDT.BN"]},
    "entry_rules": {"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
    "exit_rules": {"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}},
    "guardrails": {"per_order_notional_usdt": 100, "max_orders_per_day": 3},
}


def _spec(**over):
    import copy
    s = copy.deepcopy(_SPEC)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k] = {**s[k], **v}
        else:
            s[k] = v
    return s


def _full(d):
    from crypto_intel_engine.dsl import CryptoStrategySpec
    return CryptoStrategySpec(**d).model_dump()


@pytest.fixture
def clean():
    yield
    s = get_session()
    try:
        # ⚠️ `CryptoArenaSwitch` 必须一起清：冷却期是**全局**查最近一条切换，
        # 留着会让下一个用例的冷却期从上一个用例的切换开始算 —— 用例之间串味。
        for m in (CryptoStrategyRun, CryptoPendingOrder, CryptoStrategyProposal,
                  CryptoArenaSwitch, CryptoStrategy):
            s.query(m).delete()
        s.commit()
    finally:
        s.close()


# ── 1. diff 的正确性 ──────────────────────────────────────────────────────

def test_identical_specs_have_no_diff():
    assert pr.diff_specs(_full(_spec()), _full(_spec())) == []
    assert "没有实际改动" in pr.diff_text([])


def test_diff_catches_scalar_change_with_human_label():
    d = pr.diff_specs(_full(_spec()), _full(_spec(interval_minutes=15)))
    assert len(d) == 1
    assert d[0]["label"] == "tick 节奏（分钟）"
    assert (d[0]["from"], d[0]["to"]) == ("30", "15")


def test_diff_renders_condition_tree_as_human_text():
    """条件树整棵渲染成人话 —— 嵌套结构做字段级 diff 反而看不懂。"""
    new = _spec(entry_rules={"when": {"all_of": [
        {"field": "composite", "op": "gte", "value": 55}]}})
    d = pr.diff_specs(_full(_spec()), _full(new))
    assert len(d) == 1 and d[0]["path"] == "entry_rules.when"
    assert "composite ≥ 60" in d[0]["from"]
    assert "composite ≥ 55" in d[0]["to"]


def test_safety_changes_sort_first_and_are_marked():
    """🔒 动到护栏/仓位/运行模式的改动必须排最前 + 标出来。

    Jason 一眼先看到的应该是「单笔上限 100 → 5000」，不是「策略名改了」。
    """
    new = _spec(name="改了名", guardrails={"per_order_notional_usdt": 5000})
    d = pr.diff_specs(_full(_spec()), _full(new))
    assert d[0]["path"] == "guardrails.per_order_notional_usdt"
    assert d[0]["safety"] is True
    assert d[-1]["path"] == "name" and d[-1]["safety"] is False
    txt = pr.diff_text(d)
    assert txt.startswith("🔒")
    assert "安全边界" in txt


def test_unknown_path_is_shown_not_swallowed():
    """没登记 label 的字段也要显示 —— 少显示一条改动 = 让 Jason 在不知情下批准了它。"""
    d = pr.diff_specs({"foo": 1}, {"foo": 2})
    assert d and d[0]["label"] == "foo" and d[0]["to"] == "2"


def test_diff_reports_added_and_removed_fields():
    d = pr.diff_specs({"a": 1}, {"b": 2})
    paths = {x["path"] for x in d}
    assert paths == {"a", "b"}
    assert next(x for x in d if x["path"] == "a")["to"] == "（未设置）"


# ── 2. 提案落库与定案 ─────────────────────────────────────────────────────

def _mk_strategy():
    from crypto_intel_engine.dsl import CryptoStrategySpec
    return svc.compile_and_persist(CryptoStrategySpec(**_spec()), do_backtest=False)


def _mk_proposal(base, **over):
    new = _full(_spec(**(over or {"interval_minutes": 15})))
    return pr.create_proposal(
        base_strategy_id=base["strategy_id"], family_id=base["summary"]["family_id"],
        shortfall="30 分钟一 tick，短波段全错过",
        change_summary="tick 节奏 30 → 15 分钟",
        rationale="入场条件命中后价格常在 20 分钟内走完",
        expected_return_pct=0.08, expected_win_rate=0.55, horizon_days=30,
        new_spec=new, diff=pr.diff_specs(_full(_spec()), new))


def test_proposal_persists_the_falsifiable_claim(clean):
    base = _mk_strategy()
    p = _mk_proposal(base)
    got = pr.get_proposal(p["proposal_id"])
    # ⭐ 这两个数是整个计划最漂亮的一环：将来拿真实盈亏给 AI 打分
    assert got["expected_return_pct"] == 0.08
    assert got["expected_win_rate"] == 0.55
    assert got["horizon_days"] == 30
    assert got["status"] == "proposed"
    assert got["new_spec"]["interval_minutes"] == 15


def test_mark_decided_is_idempotent_against_double_apply(clean):
    """⛔ 点两次确认不许生成两个新版本 —— 第二个的 base 已经过时了。"""
    base = _mk_strategy()
    p = _mk_proposal(base)
    first = pr.mark_decided(p["proposal_id"], status="applied",
                            applied_strategy_id="CS-NEW")
    assert first["status"] == "applied"
    assert pr.mark_decided(p["proposal_id"], status="applied",
                           applied_strategy_id="CS-NEW-2") is None


def test_list_proposals_filters(clean):
    base = _mk_strategy()
    _mk_proposal(base)
    _mk_proposal(base, name="另一个改法")
    assert len(pr.list_proposals(status="proposed")) == 2
    assert len(pr.list_proposals(family_id=base["summary"]["family_id"])) == 2
    assert pr.list_proposals(status="applied") == []


# ── 3. 工具层端到端 ───────────────────────────────────────────────────────

def test_tool_rejects_empty_proposal(clean):
    """空提案比错提案更浪费时间：Jason 会打开、读完、发现什么都没改。"""
    from agents.tools.crypto_strategy_tools import propose_strategy_change
    base = _mk_strategy()
    r = propose_strategy_change(
        base_strategy_id=base["strategy_id"], shortfall="不太行",
        change_summary="调一下", rationale="感觉可以",
        expected_return_pct=0.05, expected_win_rate=0.5, horizon_days=30,
        new_spec=_full(_spec()))
    assert r.business_result == "negative"
    assert "完全相同" in r.message
    assert pr.list_proposals() == []          # 没落库


def test_apply_creates_next_version_without_arming(clean):
    from agents.tools.crypto_strategy_tools import (
        apply_strategy_proposal,
        propose_strategy_change,
    )
    base = _mk_strategy()
    svc.arm(base["strategy_id"])
    prop = propose_strategy_change(
        base_strategy_id=base["strategy_id"], shortfall="tick 太慢",
        change_summary="30→15 分钟", rationale="短波段走得快",
        expected_return_pct=0.08, expected_win_rate=0.55, horizon_days=30,
        new_spec=_full(_spec(interval_minutes=15)))
    pid = prop.data["proposal_id"]

    r = apply_strategy_proposal(pid)
    assert r.data["version"] == 2
    assert r.data["summary"]["enabled"] is False          # ⛔ 不自动上线
    assert svc.get_strategy(base["strategy_id"])["status"] == "armed"   # 旧版照跑
    assert pr.get_proposal(pid)["status"] == "applied"

    # 重复应用要被挡住
    again = apply_strategy_proposal(pid)
    assert again.business_result == "negative"


def test_arm_tool_supersedes_and_says_so(clean):
    from agents.tools.crypto_strategy_tools import arm_crypto_strategy
    from crypto_intel_engine.dsl import CryptoStrategySpec

    base = _mk_strategy()
    svc.arm(base["strategy_id"])
    f = svc.fork_version(base["strategy_id"],
                         CryptoStrategySpec(**_spec(interval_minutes=15)),
                         do_backtest=False)
    r = arm_crypto_strategy(f["strategy_id"])
    assert r.data["superseded"] == [base["strategy_id"]]
    assert "已停跑" in r.message


def test_arm_preview_shows_what_gets_replaced(clean):
    """确认框必须说清「你换掉的是什么」—— arm 会让旧版本立刻停跑。"""
    from agents.tools.crypto_strategy_tools import _arm_preview
    from crypto_intel_engine.dsl import CryptoStrategySpec

    base = _mk_strategy()
    svc.arm(base["strategy_id"])
    f = svc.fork_version(base["strategy_id"],
                         CryptoStrategySpec(**_spec(interval_minutes=15)),
                         do_backtest=False)
    prev = _arm_preview({"strategy_id": f["strategy_id"]})
    assert base["strategy_id"] in prev["将替换掉"]
    assert "会立刻停跑" in prev["将替换掉"]
    assert "30 → 15" in prev["两版差异"]


def test_proposal_preview_puts_the_diff_above_the_ai_story(clean):
    """预览里 diff 是事实，AI 的说法只是说法 —— 两者都在，但 diff 必须在。"""
    from agents.tools.crypto_strategy_tools import _proposal_preview
    base = _mk_strategy()
    p = _mk_proposal(base)
    prev = _proposal_preview({"proposal_id": p["proposal_id"]})
    assert "30 → 15" in prev["实际改动（系统逐字段比对）"]
    assert prev["AI 说的调整"]                       # AI 的说法也保留
    assert "预期收益" in prev["AI 的可证伪断言"]


def test_proposal_preview_blocks_already_decided(clean):
    from agents.tools.crypto_strategy_tools import _proposal_preview
    base = _mk_strategy()
    p = _mk_proposal(base)
    pr.mark_decided(p["proposal_id"], status="rejected")
    assert "error" in _proposal_preview({"proposal_id": p["proposal_id"]})


def test_confirm_gate_covers_the_two_new_tools():
    """新增受确认门的工具必须归类，否则会混进胜率分母（P0-4 的老病）。"""
    from common.decision_kind import CONFIRMED_TOOL_KINDS, OPS
    assert CONFIRMED_TOOL_KINDS["apply_strategy_proposal"] == OPS
    assert CONFIRMED_TOOL_KINDS["arm_crypto_strategy"] == OPS


# ── 4. 复审补的回归 ───────────────────────────────────────────────────────

def test_mode_never_shows_up_in_diff(clean):
    """🔴 回归：`mode` 是运行态不是策略内容，进 diff 会让**每次 arm 预览都冒假的 🔒 告警**。

    `arm()` 无条件写 live、`enable_paper()` 无条件写 paper，所以拿 armed 的 v1 比
    draft 的 v2 永远得到「🔒 运行模式：live → paper」，而 arm 完立刻就是 live。
    🔒 的全部价值在于它很少出现，每次都亮等于训练 Jason 无视它。
    """
    d = pr.diff_specs(_full(_spec(mode="live")), _full(_spec(mode="paper")))
    assert d == []
    d2 = pr.diff_specs(_full(_spec(mode="live")),
                       _full(_spec(mode="paper", interval_minutes=15)))
    assert [x["path"] for x in d2] == ["interval_minutes"]


def test_stale_proposal_is_refused_not_silently_applied(clean):
    """🔴🔴 回归：同族两份未决提案，后批准的不许**静默回滚**先批准的改动。

    实测过的翻车链路：
        P1(tick 30→15) 批准 → v2: tick=15
        P2(单笔 100→800，基于 v1) 批准 → v3: **tick 被还原成 30**、单笔=800
    而 P2 的确认框只会显示「单笔 100 → 800」，一个字都不提 tick 被改回去了。
    """
    from agents.tools.crypto_strategy_tools import (
        _proposal_preview,
        apply_strategy_proposal,
        propose_strategy_change,
    )
    base = _mk_strategy()
    common = dict(shortfall="不行", rationale="因为",
                  expected_return_pct=0.05, expected_win_rate=0.5, horizon_days=30)
    p1 = propose_strategy_change(base_strategy_id=base["strategy_id"],
                                 change_summary="tick 30→15",
                                 new_spec=_full(_spec(interval_minutes=15)),
                                 **common).data["proposal_id"]
    p2 = propose_strategy_change(base_strategy_id=base["strategy_id"],
                                 change_summary="单笔 100→800",
                                 new_spec=_full(_spec(
                                     guardrails={"per_order_notional_usdt": 800})),
                                 **common).data["proposal_id"]

    r1 = apply_strategy_proposal(p1)
    assert r1.data["version"] == 2
    # 同族其它提案在**批准那一刻**就被标过期，不用等 Jason 点进去才发现
    assert r1.data["superseded_proposals"] == [p2]

    # 预览层要挡，且说的是人话不是内部状态名
    err = _proposal_preview({"proposal_id": p2}).get("error", "")
    assert "过期" in err and "静默还原" in err
    # 直接调函数（绕过确认门）也要挡
    r2 = apply_strategy_proposal(p2)
    assert r2.business_result == "negative"
    assert "过期" in r2.message

    # 最要紧的：v2 的 tick 没被还原，且没有第三个版本冒出来
    fam = svc.list_family(base["summary"]["family_id"])
    assert [x["version"] for x in fam] == [1, 2]
    assert svc.get_strategy(r1.data["strategy_id"])["spec"]["interval_minutes"] == 15


def test_staleness_reports_versions(clean):
    base = _mk_strategy()
    p = _mk_proposal(base)
    assert pr.staleness(p["proposal_id"])["stale"] is False
    from crypto_intel_engine.dsl import CryptoStrategySpec
    svc.fork_version(base["strategy_id"], CryptoStrategySpec(**_spec(interval_minutes=20)),
                     do_backtest=False)
    st = pr.staleness(p["proposal_id"])
    assert st["stale"] is True and st["base_version"] == 1 and st["latest_version"] == 2


def test_standings_hides_archived_versions_by_default(clean):
    """软退役之后历史版本会永久累积（旧实现是删的），默认列表不该被它们淹掉。"""
    from crypto_strategy.performance import standings
    base = _mk_strategy()
    svc.arm(base["strategy_id"])
    from crypto_intel_engine.dsl import CryptoStrategySpec
    f = svc.fork_version(base["strategy_id"], CryptoStrategySpec(**_spec(interval_minutes=15)),
                         do_backtest=False)
    svc.arm(f["strategy_id"])                       # base 变 superseded

    ids = {r["strategy_id"] for r in standings()}
    assert ids == {f["strategy_id"]}
    all_ids = {r["strategy_id"] for r in standings(include_archived=True)}
    assert all_ids == {base["strategy_id"], f["strategy_id"]}
    # 多版本同名，必须带版本号才分得清
    assert all(r.get("version") for r in standings(include_archived=True))
