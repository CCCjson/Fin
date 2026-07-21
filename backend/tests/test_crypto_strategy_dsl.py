"""需求3 阶段1：DSL 校验 + 纯函数评估器（离线，无网络）。"""
import pytest
from pydantic import ValidationError

from crypto_intel_engine.dsl import (
    Condition,
    ConditionGroup,
    CostModel,
    CryptoStrategySpec,
    EntryRules,
    ExitRules,
    Guardrails,
    Universe,
    resolve_field,
    round_trip_cost,
)
from crypto_strategy.evaluator import evaluate

# ──────────────────── DSL 校验 ────────────────────


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        Condition(field="not_a_field", op="gt", value=50)


def test_categorical_field_rejects_numeric_op():
    with pytest.raises(ValidationError):
        Condition(field="btc_regime", op="gt", value="bull")


def test_numeric_field_rejects_in_op():
    with pytest.raises(ValidationError):
        Condition(field="composite", op="in", value=[1, 2])


def test_between_requires_pair():
    with pytest.raises(ValidationError):
        Condition(field="composite", op="between", value=[50])
    Condition(field="composite", op="between", value=[40, 60])   # ok


def test_universe_requires_bn_suffix():
    with pytest.raises(ValidationError):
        Universe(symbols=["BTCUSDT"])
    u = Universe(symbols=["btcusdt.bn"])
    assert u.symbols == ["BTCUSDT.BN"]


def test_empty_condition_group_rejected():
    with pytest.raises(ValidationError):
        ConditionGroup()


def _mk_spec(kind="swing", min_net_edge=0.0, cost=None):
    return CryptoStrategySpec(
        name="t", strategy_kind=kind,
        universe=Universe(symbols=["BTCUSDT.BN"]),
        entry_rules=EntryRules(when=ConditionGroup(
            all_of=[Condition(field="composite", op="gte", value=60)])),
        exit_rules=ExitRules(when=ConditionGroup(
            all_of=[Condition(field="composite", op="lt", value=40)])),
        cost_model=cost or CostModel(min_net_edge_pct=min_net_edge),
        guardrails=Guardrails(per_order_notional_usdt=30, max_orders_per_day=6),
    )


def test_arb_requires_edge_above_round_trip_cost():
    # 默认成本：2*0.001 + 0.0005 = 0.0025 往返；arb 的 min_net_edge 必须 > 0.0025
    with pytest.raises(ValidationError):
        _mk_spec(kind="arb", min_net_edge=0.001)     # 低于往返成本 → 拒
    spec = _mk_spec(kind="arb", min_net_edge=0.01)   # 高于往返成本 → ok
    assert spec.strategy_kind == "arb"


def test_swing_no_edge_constraint():
    _mk_spec(kind="swing", min_net_edge=0.0)         # 波段不强制，允许 0


def test_round_trip_cost_math():
    assert round_trip_cost(CostModel(taker_fee_pct=0.001, slippage_pct=0.0005)) == pytest.approx(0.0025)


# ──────────────────── 字段取值 + 评估器真值表 ────────────────────

_ANALYSIS = {
    "composite": 68.0,
    "recommendation": "BUY",
    "dimensions": {"technical": {"score": 72.0}, "derivatives": {"score": 40.0},
                   "regime": {"score": 30.0}},
    "derivatives_snapshot": {"funding": {"funding_rate": -0.0006},
                             "long_short": {"ratio": 1.4}},
    "screen": {"verdict": "pass", "score": 88},
    "btc_regime": {"regime": "bull"},
    "market_context": {"fear_greed": {"value": "25"}},
    "price": {"latest": 60000, "change_5d_pct": -3.2, "change_20d_pct": 5.0, "change_60d_pct": 12.0},
    "suggested_position_pct": 1.5, "current_position_pct": 0.0,
}


def test_resolve_nested_and_categorical():
    assert resolve_field("dim.technical", _ANALYSIS) == 72.0
    assert resolve_field("funding_rate", _ANALYSIS) == -0.0006
    assert resolve_field("btc_regime", _ANALYSIS) == "bull"
    assert resolve_field("fear_greed", _ANALYSIS) == 25.0     # 字符串 '25' → 25.0
    assert resolve_field("long_short_ratio", _ANALYSIS) == 1.4


def test_resolve_missing_returns_none():
    assert resolve_field("funding_rate", {}) is None


def test_evaluate_all_of_and():
    g = ConditionGroup(all_of=[
        Condition(field="btc_regime", op="eq", value="bull"),
        Condition(field="funding_rate", op="lt", value=-0.0005),
        Condition(field="dim.technical", op="gte", value=60),
    ])
    matched, fired = evaluate(g, _ANALYSIS)
    assert matched is True
    assert len(fired) == 3


def test_evaluate_all_of_fails_on_one():
    g = ConditionGroup(all_of=[
        Condition(field="btc_regime", op="eq", value="bear"),   # 实际 bull → 挂
        Condition(field="dim.technical", op="gte", value=60),
    ])
    matched, _ = evaluate(g, _ANALYSIS)
    assert matched is False


def test_evaluate_any_of_or():
    g = ConditionGroup(
        all_of=[Condition(field="composite", op="gte", value=60)],
        any_of=[Condition(field="funding_rate", op="gt", value=0.01),   # 假
                Condition(field="screen.verdict", op="in", value=["pass", "caution"])],  # 真
    )
    matched, _ = evaluate(g, _ANALYSIS)
    assert matched is True


def test_evaluate_missing_data_not_matched():
    g = ConditionGroup(all_of=[Condition(field="funding_rate", op="lt", value=0)])
    matched, _ = evaluate(g, {})    # 缺数据 → 不满足
    assert matched is False
