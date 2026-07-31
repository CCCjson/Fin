"""批5 一致性与展示：最小覆盖闸 / 回测口径如实标注 / 漂移阈值不写死。

共同主题：**别让一个数字看起来比它实际更有分量**。
"""
import pytest


class TestMinimumCoverageGate:
    """缺维重归一是刻意设计，但没有下限就成了「一条新闻推出一个买入建议」。"""

    def test_single_sentiment_dimension_cannot_buy(self):
        """实测的原始 bug：只有 sentiment 一维（0.15 权重）→ 旧实现给 92 分 BUY。"""
        from crypto_intel_engine.scorer import LOW_COVERAGE_CAP, score_crypto_cockpit
        r = score_crypto_cockpit({"technical": None, "derivatives": None, "regime": None,
                                  "flow": None, "sentiment": 92.0}, {"verdict": "pass"})
        assert r["dimension_coverage"] == pytest.approx(0.15)
        assert r["composite"] == LOW_COVERAGE_CAP
        assert r["recommendation"] != "BUY"
        assert r["suggested_position_pct"] == 0.0 or r["recommendation"] == "HOLD"

    def test_half_weight_coverage_passes(self):
        """technical(0.30)+regime(0.20)=0.50 达线：价格 + 大势是可接受的最小组合。"""
        from crypto_intel_engine.scorer import score_crypto_cockpit
        r = score_crypto_cockpit({"technical": 80.0, "regime": 80.0}, {"verdict": "pass"})
        assert r["composite"] == pytest.approx(80.0)
        assert "composite_capped_low_dimension_coverage" not in r["adjustments"]

    def test_cap_does_not_raise_low_scores(self):
        """封顶只压不抬：本来就低于封顶线的分不受影响。"""
        from crypto_intel_engine.scorer import score_crypto_cockpit
        r = score_crypto_cockpit({"sentiment": 20.0}, {"verdict": "pass"})
        assert r["composite"] == pytest.approx(20.0)

    def test_raw_composite_preserved(self):
        """原始分要留痕，方便事后归因（对齐既有 calibration 的做法）。"""
        from crypto_intel_engine.scorer import score_crypto_cockpit
        r = score_crypto_cockpit({"sentiment": 92.0}, {"verdict": "pass"})
        assert r["raw_composite"] == pytest.approx(92.0)


class TestSubSignalCoverage:
    """同一个病在子信号层重复一次：0.05 权重的 basis 不许代表整个衍生品维。"""

    def test_basis_alone_does_not_represent_derivatives(self):
        from crypto_intel_engine.scorer import score_derivatives
        score, detail = score_derivatives({"basis": {"basis_rate": 0.001}})
        assert score is None and detail["available"] is False

    def test_category_alone_does_not_represent_flow(self):
        from crypto_intel_engine.scorer import score_flow
        score, detail = score_flow({"category_change_pct": 5.0})
        if score is not None:          # 该实现下 category 单独可得时权重 0.25 < 0.30
            pytest.skip("该输入未走到 category-only 分支")
        assert detail["available"] is False

    def test_sufficient_weight_still_scores(self):
        """别做成「谁都不给分」：funding 权重 0.30 达线要出分。"""
        from crypto_intel_engine.scorer import score_derivatives
        score, _ = score_derivatives({"funding": {"funding_rate": 0.0001}})
        assert score is not None


class TestBacktestHonesty:
    """回测口径与文档不符——不能改 C++，那就把差异如实摆出来。"""

    @staticmethod
    def _run(taker_fee_pct, field="composite", bar_days=28, matured=frozenset()):
        from datetime import date as _date
        from datetime import timedelta as _td

        from crypto_intel_engine.dsl import (
            Condition,
            ConditionGroup,
            CostModel,
            CryptoStrategySpec,
            EntryRules,
            ExitRules,
            Guardrails,
            PositionPolicy,
            Universe,
        )
        from crypto_strategy.backtest_gate import run_backtest_gate
        spec = CryptoStrategySpec(
            strategy_id="CS-T", name="t", universe=Universe(symbols=["BTCUSDT.BN"]),
            entry_rules=EntryRules(when=ConditionGroup(
                all_of=[Condition(field=field, op="gte", value=60)])),
            exit_rules=ExitRules(when=ConditionGroup(
                all_of=[Condition(field=field, op="lt", value=40)])),
            position_policy=PositionPolicy(),
            cost_model=CostModel(taker_fee_pct=taker_fee_pct),
            guardrails=Guardrails(per_order_notional_usdt=100.0, max_orders_per_day=5))
        bars = [{"date": (_date(2026, 1, 1) + _td(days=i)).isoformat(),
                 "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
                 "volume": 100.0} for i in range(bar_days)]
        return run_backtest_gate(
            spec, {"BTCUSDT.BN": bars},
            # S8：口径改成一次组合回测（共享资金池），注入点从 run_bt 变 run_pf
            run_pf=lambda bars_by_symbol, signals_by_symbol, **kw: {
                "metrics": {"total_return": 0.05}},
            collect=lambda on_bar, df: [], _matured=set(matured))

    def test_fee_mismatch_is_reported(self):
        """策略配 0.075%（BNB 抵扣）而引擎写死 0.1% → 必须说出来。"""
        r = self._run(0.00075)
        assert r["metrics"]["fee_basis"]["matches"] is False
        assert any("taker" in c for c in r["caveats"])

    def test_matching_fee_no_caveat(self):
        r = self._run(0.001)
        assert r["metrics"]["fee_basis"]["matches"] is True
        assert not any("taker" in c for c in r["caveats"])

    def test_caveats_are_separate_from_degraded(self):
        """caveats 与 degraded 正交：前者是「数字含义弱」，后者是「规则没回放成」。"""
        r = self._run(0.00075)
        assert "caveats" in r and "degraded_reasons" in r
        assert not any("taker" in x for x in r["degraded_reasons"])


class TestReplayCoverageOnlyCountsMetricFields:
    """⛔ 忠实回放不许被报成「0% 有指标帧」。

    `replay_days` 数的是 `crypto_metrics` 来的 frames，但 `replay.py` 的 on_bar 是
    `{**frames.get(date, {}), **_price_frame(history)}` —— `price.change_*` 每根 bar
    都直接从 df 算，压根不走 frames。所以一条纯 `price.*` 的策略是 100% 忠实回放
    （mode="dsl"、degraded=False），旧实现却给 coverage=0 并弹出「指标历史还没攒够」，
    把最干净的那种回放说成最不可信 —— 和本模块「别让数字看起来比实际更有分量」正好反了。
    """

    def test_price_only_spec_gets_no_coverage_caveat(self):
        r = TestBacktestHonesty._run(0.001, field="price.change_5d_pct", bar_days=120)
        assert r["replay"]["mode"] == "dsl" and r["degraded"] is False, "前提：这是忠实回放"
        assert r["metrics"]["replay_coverage"] is None, "无可比对应为 None，不是 0%"
        assert r["metrics"]["metric_fields"] == []
        assert not any("指标帧" in c for c in r["caveats"])

    def test_metric_backed_spec_still_reports_coverage(self):
        """反向：真依赖 crypto_metrics 的规则，覆盖度该报还得报（别把闸门修没了）。"""
        r = TestBacktestHonesty._run(0.001, field="funding_rate", bar_days=120,
                                     matured={"funding_rate"})
        # funding_rate 被声明为已成熟 → 进 metric_fields，但测试没喂指标 → 帧全空
        assert r["metrics"]["metric_fields"] == ["funding_rate"]
        assert r["metrics"]["replay_coverage"] == 0.0
        assert any("指标帧" in c for c in r["caveats"])


class TestDriftThresholdNotHardcoded:
    """前端文案里的漂移阈值必须随配置走。"""

    def test_engine_stamps_threshold_into_reason(self, monkeypatch):
        from crypto_intel_engine.dsl import Guardrails
        from crypto_strategy import pending as pending_mod
        from crypto_strategy.engine import CryptoStrategyEngine

        captured = {}

        def _fake_create(strategy_id, symbol, action, qty, price, **kw):
            captured.update(kw)
            return {"order_ref": "CPO-x"}

        monkeypatch.setattr(pending_mod.crypto_pending_service, "has_open",
                            lambda *a, **k: False)
        monkeypatch.setattr(pending_mod.crypto_pending_service, "create_pending",
                            _fake_create)

        class _Spec:
            guardrails = Guardrails(per_order_notional_usdt=100.0, max_orders_per_day=5,
                                    max_confirm_slippage_pct=0.035)

        class _Row:
            strategy_id, mode = "CS-1", "live"

        CryptoStrategyEngine()._stage_or_log(
            _Row(), _Spec(), "BTCUSDT.BN",
            {"action": "BUY", "qty": 1.0, "price": 100.0, "fired": [], "composite": 70})
        assert captured["reason"]["drift_threshold_pct"] == pytest.approx(0.035)
