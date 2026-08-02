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


def test_universe_normalizes_case():
    """`Universe` 只做大写归一。

    ⚠️ 后缀校验**挪到了 spec 层**（S5 批次1）：后缀规则跟市场绑
    （crypto `.BN` / A 股 `.SH/.SZ/.BJ` / 美股裸 ticker），而 `Universe`
    自己不知道它属于哪个市场 —— 在这里写死 `.BN` 会让股票策略连编译都过不了。
    真正的把关见 `test_crypto_spec_rejects_a_share_symbols`。
    """
    u = Universe(symbols=["btcusdt.bn"])
    assert u.symbols == ["BTCUSDT.BN"]
    with pytest.raises(ValidationError):
        Universe(symbols=["  "])


def test_crypto_spec_rejects_a_share_symbols():
    """🔴 市场写错时必须**编译期就炸**。

    DSL 的语义是「取不到值 = 该条不满足」，所以市场写错的话字段表也会取错，
    **所有条件静默永不触发** —— 策略一单不下，而每一层看上去都正常。
    """
    base = {"name": "t", "guardrails": {"per_order_notional_usdt": 100,
                                        "max_orders_per_day": 3},
            "entry_rules": {"when": {"all_of": [
                {"field": "composite", "op": "gte", "value": 60}]}},
            "exit_rules": {"when": {"all_of": [
                {"field": "composite", "op": "lt", "value": 40}]}}}

    # crypto 策略写了 A 股标的
    with pytest.raises(ValidationError, match="不属于 crypto"):
        CryptoStrategySpec(**base, market="crypto",
                           universe={"symbols": ["600519.SH"]})
    # 裸 BTCUSDT 会被 `infer_market_from_symbol` 判成美股 —— 所以 crypto 必须带 .BN
    with pytest.raises(ValidationError, match="不属于 crypto"):
        CryptoStrategySpec(**base, market="crypto",
                           universe={"symbols": ["BTCUSDT"]})
    # 反向：A 股策略写了币
    with pytest.raises(ValidationError, match="不属于 a_share"):
        CryptoStrategySpec(**base, market="a_share",
                           universe={"symbols": ["BTCUSDT.BN"]})


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


# ── 组合策略 DSL：子策略 + 权重（S8 批次3，裁决 6）────────────────────────

class TestSubStrategies:
    """裁决 6 那句「60% 趋势 + 40% 均值回归」，在此之前 DSL 里**表达不出来**。"""

    @staticmethod
    def _base(**over):
        d = {
            "name": "组合", "universe": {"symbols": ["BTCUSDT.BN", "ETHUSDT.BN"]},
            "guardrails": {"per_order_notional_usdt": 100, "max_orders_per_day": 3},
        }
        d.update(over)
        return d

    @staticmethod
    def _sub(name, weight, symbols):
        return {
            "name": name, "weight": weight, "universe": {"symbols": symbols},
            "entry_rules": {"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
            "exit_rules": {"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}},
        }

    def test_combo_compiles_and_rule_sets_carry_weights(self):
        spec = CryptoStrategySpec(**self._base(sub_strategies=[
            self._sub("趋势", 0.6, ["BTCUSDT.BN"]),
            self._sub("均值回归", 0.4, ["ETHUSDT.BN"])]))
        rs = spec.rule_sets()
        assert [(r.name, r.weight) for r in rs] == [("趋势", 0.6), ("均值回归", 0.4)]
        assert spec.owner_of("BTCUSDT.BN").name == "趋势"
        assert spec.owner_of("SOLUSDT.BN") is None

    def test_plain_strategy_normalizes_to_one_rule_set(self):
        """⭐ 单策略与组合策略在 `rule_sets()` 之后**长成同一个形状**。

        下游（回测闸 / 逐日回放 / 实盘引擎 / agent 工具）因此只需要写一套遍历逻辑，
        不用到处 `if spec.sub_strategies:` 分叉 —— 那种分叉迟早漏一处
        （批次3 实施时就漏了 `_non_replayable_fields`，被测试抓出来）。
        """
        spec = CryptoStrategySpec(**self._base(
            entry_rules={"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
            exit_rules={"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}}))
        rs = spec.rule_sets()
        assert len(rs) == 1
        assert rs[0].weight == 1.0
        assert rs[0].universe.symbols == ["BTCUSDT.BN", "ETHUSDT.BN"]

    def test_both_top_level_and_subs_is_rejected(self):
        """⛔ 两套规则并存 =「按谁的规则交易」没有答案，编译期就得炸。"""
        with pytest.raises(ValidationError, match="不能再给顶层"):
            CryptoStrategySpec(**self._base(
                entry_rules={"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
                exit_rules={"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}},
                sub_strategies=[self._sub("a", 0.5, ["BTCUSDT.BN"]),
                                self._sub("b", 0.5, ["ETHUSDT.BN"])]))

    def test_neither_is_rejected(self):
        with pytest.raises(ValidationError, match="必须同时给"):
            CryptoStrategySpec(**self._base())

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValidationError, match="权重合计必须等于 1.0"):
            CryptoStrategySpec(**self._base(sub_strategies=[
                self._sub("a", 0.5, ["BTCUSDT.BN"]),
                self._sub("b", 0.4, ["ETHUSDT.BN"])]))

    def test_overlapping_universes_are_rejected(self):
        """🔴 v1 硬限制：一个币只有一个仓位，同根 bar 上 A 买 B 卖无法净额（现货不能做空）。"""
        with pytest.raises(ValidationError, match="不支持重叠"):
            CryptoStrategySpec(**self._base(sub_strategies=[
                self._sub("a", 0.5, ["BTCUSDT.BN"]),
                self._sub("b", 0.5, ["BTCUSDT.BN"])]))

    def test_sub_symbol_outside_top_universe_is_rejected(self):
        """顶层 universe 是总闸，子策略只能在它范围内切分。"""
        with pytest.raises(ValidationError, match="不在顶层 universe"):
            CryptoStrategySpec(**self._base(sub_strategies=[
                self._sub("a", 0.5, ["BTCUSDT.BN"]),
                self._sub("b", 0.5, ["SOLUSDT.BN"])]))

    def test_single_sub_is_rejected(self):
        """同一件事有两种写法，迟早会读错。"""
        with pytest.raises(ValidationError, match="至少要两条"):
            CryptoStrategySpec(**self._base(sub_strategies=[
                self._sub("只有一条", 1.0, ["BTCUSDT.BN"])]))

    def test_duplicate_sub_names_are_rejected(self):
        with pytest.raises(ValidationError, match="子策略名重复"):
            CryptoStrategySpec(**self._base(sub_strategies=[
                self._sub("同名", 0.5, ["BTCUSDT.BN"]),
                self._sub("同名", 0.5, ["ETHUSDT.BN"])]))

    def test_round_trips_through_json(self):
        """落库 / 提案 diff 都靠 JSON 往返，别在这层丢字段。"""
        spec = CryptoStrategySpec(**self._base(sub_strategies=[
            self._sub("趋势", 0.6, ["BTCUSDT.BN"]),
            self._sub("均值回归", 0.4, ["ETHUSDT.BN"])]))
        again = CryptoStrategySpec.model_validate(spec.model_dump())
        assert [(r.name, r.weight) for r in again.rule_sets()] == \
               [(r.name, r.weight) for r in spec.rule_sets()]


# ── 市场无关的字段注册表（S5 批次1）────────────────────────────────────────

class TestMarketAwareFields:
    """🔴 跨市场字段**必须编译期炸**，因为它的运行期表现是「静默永不触发」。

    DSL 的语义是「取不到值 = 该条不满足」。所以一条 A 股策略里写了 `funding_rate`
    （crypto 的字段）时不会报错 —— 它会一单不下，看上去像「行情一直没到条件」。
    这种失败模式在真钱系统里最难查：每一层看上去都正常。
    """

    _G = {"per_order_notional_usdt": 100, "max_orders_per_day": 3}

    @classmethod
    def _mk(cls, market, symbols, field):
        return CryptoStrategySpec(
            name="t", market=market, guardrails=cls._G,
            universe={"symbols": symbols},
            entry_rules={"when": {"all_of": [{"field": field, "op": "lt", "value": 20}]}},
            exit_rules={"when": {"all_of": [
                {"field": "composite", "op": "lt", "value": 40}]}})

    def test_stock_spec_accepts_stock_fields(self):
        assert self._mk("a_share", ["600519.SH"], "valuation.pe_ttm").market == "a_share"
        assert self._mk("us_stock", ["AAPL"], "valuation.pe_ttm").market == "us_stock"

    def test_crypto_stays_the_default(self):
        """存量数据里没有 market 字段，读回来必须仍是 crypto。"""
        s = self._mk("crypto", ["BTCUSDT.BN"], "funding_rate")
        assert s.market == "crypto"
        again = CryptoStrategySpec.model_validate(
            {k: v for k, v in s.model_dump().items() if k != "market"})
        assert again.market == "crypto"

    def test_stock_spec_rejects_crypto_fields(self):
        with pytest.raises(ValidationError, match="是 crypto 的字段"):
            self._mk("a_share", ["600519.SH"], "funding_rate")

    def test_crypto_spec_rejects_stock_fields(self):
        with pytest.raises(ValidationError, match="crypto 没有"):
            self._mk("crypto", ["BTCUSDT.BN"], "valuation.pe")

    def test_error_says_which_market_the_field_belongs_to(self):
        """⭐ 报错要**说得清**，不是干巴巴一句「未知字段」。"""
        with pytest.raises(ValidationError) as e:
            self._mk("a_share", ["600519.SH"], "funding_rate")
        msg = str(e.value)
        assert "crypto" in msg and "静默永不触发" in msg

    def test_hk_stock_is_not_registered(self):
        """港股 Jason 已拍板不做 —— ⛔ 不许静默回落到 A 股的字段表。"""
        from common.strategy_fields import registry_for
        with pytest.raises(KeyError, match="还没有字段注册表"):
            registry_for("hk_stock")

    def test_ml_dimension_is_deliberately_absent_for_stocks(self):
        """⛔ `dim.ml` 刻意不给股票用：项目里一个训好的模型都没有（P1-6 未开工），
        暴露它 = 邀请 AI 写出永远不触发的规则。"""
        from common.strategy_fields import explain_unknown, known_fields
        assert "dim.ml" not in known_fields("a_share")
        assert "刻意不提供" in explain_unknown("a_share", "dim.ml")

    def test_evaluator_uses_the_right_market_table(self):
        """同一张分析卡、同一个字段名，在两个市场下取到的是**不同的路径**。"""
        from crypto_strategy.evaluator import evaluate

        stock_card = {"dimensions": {"fundamental": {"score": 70}}}
        group = ConditionGroup(all_of=[
            Condition(field="dim.fundamental", op="gte", value=60)])
        ok, _ = evaluate(group, stock_card, market="a_share")
        assert ok is True
        # crypto 表里没有 dim.fundamental → 取不到 → 不满足（而不是报错）
        ok2, _ = evaluate(group, stock_card, market="crypto")
        assert ok2 is False
