"""需求3 阶段2：回测闸 + service arm 武装闸（内存库，mock C++ 回测）。"""
import pytest

from crypto_intel_engine.dsl import (
    Condition,
    ConditionGroup,
    CostModel,
    CryptoStrategySpec,
    EntryRules,
    ExitRules,
    Guardrails,
    Universe,
)
from crypto_strategy.backtest_gate import run_backtest_gate


@pytest.fixture
def mem_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from data_engine.storage import database as db
    from data_engine.storage.models import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "get_session", lambda: make())
    return make


def _spec(**over):
    base = dict(
        name="t", strategy_kind="swing",
        universe=Universe(symbols=["BTCUSDT.BN"]),
        entry_rules=EntryRules(when=ConditionGroup(
            all_of=[Condition(field="funding_rate", op="lt", value=0),
                    Condition(field="composite", op="gte", value=60)])),
        exit_rules=ExitRules(when=ConditionGroup(
            all_of=[Condition(field="composite", op="lt", value=40)])),
        cost_model=CostModel(),
        guardrails=Guardrails(per_order_notional_usdt=30, max_orders_per_day=6),
    )
    base.update(over)
    return CryptoStrategySpec(**base)


def _bars(n=60):
    return [{"date": f"2026-01-{i % 28 + 1:02d}", "open": 100 + i, "high": 101 + i,
             "low": 99 + i, "close": 100 + i, "volume": 1000} for i in range(n)]


def _fake_collect(on_bar, df):
    return [{"date": "2026-01-05", "action": "buy"}, {"date": "2026-01-20", "action": "sell"}]


# ── 回测闸 ──

def test_gate_net_positive_passes():
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):
        return {"metrics": {"total_return": 0.12, "total_trades": 4, "sharpe_ratio": 1.1}}
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars()}, run_pf=run_pf, collect=_fake_collect)
    assert r["passed"] is True
    assert r["net_return"] == pytest.approx(0.12)
    # 🔴 口径标记：这个数是**组合收益**，不是「各币独立收益的平均」
    assert r["basis"] == "portfolio_shared_capital"
    assert r["degraded"] is True                       # 有原语回放不了 → 降级
    assert any("funding_rate" in x for x in r["degraded_reasons"])   # 非技术原语被点名
    assert r["replay"]["mode"] == "proxy"              # 库里没历史 → 纯代理（与改动前同）


def test_gate_net_negative_blocked():
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):
        return {"metrics": {"total_return": -0.05, "total_trades": 30}}   # 毛赚净亏典型
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars()}, run_pf=run_pf, collect=_fake_collect)
    assert r["passed"] is False


def test_gate_uses_strategy_slippage():
    seen = {}
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):
        seen.update(kw)
        return {"metrics": {"total_return": 0.01}}
    spec = _spec(cost_model=CostModel(slippage_pct=0.002))
    run_backtest_gate(spec, {"BTCUSDT.BN": _bars()}, run_pf=run_pf, collect=_fake_collect)
    assert seen["slippage_pct"] == 0.002                # 策略自己的滑点被传进回测


def test_gate_enforces_the_cash_floor_in_backtest_too():
    """🔒 回测口径必须跟实盘风控一致：总仓位 ≤ 80%，留 20% 现金。

    ⛔ 此前回测能满仓，于是「回测过闸的策略上实盘后收益系统性低于预期」——
    高估在先。Jason 2026-07-31 拍板改成一致。
    ⚠️ 单币上限走 DSL 的 `per_symbol_exposure_cap_pct`，组合口径下它**第一次真正生效**
    （逐币独立回测时每个币都有完整一份本金，那条约束等于不存在）。
    """
    seen = {}
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):
        seen.update(kw)
        return {"metrics": {"total_return": 0.01}}
    spec = _spec()
    run_backtest_gate(spec, {"BTCUSDT.BN": _bars()}, run_pf=run_pf, collect=_fake_collect)
    rc = seen["risk_config"]
    assert rc["max_total_position_pct"] == 0.8, "20% 现金底线没传下去"
    assert rc["max_position_pct"] == spec.position_policy.per_symbol_exposure_cap_pct


def test_gate_runs_one_portfolio_not_n_independent_backtests():
    """⭐ **所有币必须在一次回测里共享同一份资金**。

    逐币各跑一遍再平均 = 每个币都有完整一份本金，没有资金竞争，
    带权重的组合策略回测出来的数字跟权重毫无关系。
    """
    calls = []
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):
        calls.append(sorted(bars_by_symbol))
        return {"metrics": {"total_return": 0.03}}
    r = run_backtest_gate(
        _spec(), {"BTCUSDT.BN": _bars(), "ETHUSDT.BN": _bars(), "SOLUSDT.BN": _bars()},
        run_pf=run_pf, collect=_fake_collect)
    assert len(calls) == 1, "三个币应该在**一次**组合回测里跑，不是各跑一遍"
    assert calls[0] == ["BTCUSDT.BN", "ETHUSDT.BN", "SOLUSDT.BN"]
    assert r["metrics"]["symbols_tested"] == 3


def test_gate_does_not_fabricate_per_symbol_returns():
    """⛔ 组合口径下**没有**「这个币赚了百分之几」这回事。

    一份共享现金拆不出逐币收益率（同一笔钱在不同币之间流动），
    硬拆一个数出来会骗人。逐币只报「下了几单、有几天数据」。
    """
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):
        return {"metrics": {"total_return": 0.05},
                "trades": [{"symbol": "BTCUSDT.BN"}, {"symbol": "BTCUSDT.BN"}],
                "bar_coverage": {"BTCUSDT.BN": 120}}
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars()},
                          run_pf=run_pf, collect=_fake_collect)
    row = next(x for x in r["per_symbol"] if x["symbol"] == "BTCUSDT.BN")
    assert row["net_return"] is None, "别编一个逐币收益率出来"
    assert row["num_trades"] == 2
    assert row["bar_days"] == 120


def test_gate_reports_cash_contention_not_silently():
    """⛔ 「多个币同日抢同一份现金」是组合口径与旧口径最大的差别，必须说出来。"""
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):
        return {"metrics": {"total_return": 0.05},
                "cash_contention": {"days": 7, "trimmed_notional": 12345.0}}
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars()},
                          run_pf=run_pf, collect=_fake_collect)
    assert r["cash_contention"]["days"] == 7
    assert any("资金竞争" in c for c in r["caveats"])


def test_gate_short_history_skipped():
    def run_pf(bars_by_symbol, signals_by_symbol, **kw):  # 不该被调用
        raise AssertionError("历史不足不应回测")
    r = run_backtest_gate(_spec(), {"BTCUSDT.BN": _bars(5)}, run_pf=run_pf, collect=_fake_collect)
    assert r["net_return"] is None and r["passed"] is False


# ── 逐日回放：成熟原语真的 gate 交易，不再只是从 degraded 名单里划掉 ──

class TestReplay:
    """⛔ 回归：`matured_fields` 曾只是「从 degraded 名单扣除」，回测里一条 DSL 都没评估过。"""

    def test_replay_paths_match_field_resolvers(self):
        """防漂移门禁：REPLAY_FIELD_PATHS 造的帧必须能被 FIELD_RESOLVERS 原样取回。

        两边对不上 → 条件静默判 False（整条闸假装没命中），是最难查的那种病。
        """
        from crypto_intel_engine.dsl import resolve_field
        from crypto_strategy.replay import REPLAY_FIELD_PATHS, _set_path

        for field, path in REPLAY_FIELD_PATHS.items():
            frame: dict = {}
            _set_path(frame, path, 4242.0)
            assert resolve_field(field, frame) == 4242.0, f"{field} 路径与 FIELD_RESOLVERS 不一致"

    def test_metric_backed_fields_are_all_pathed(self):
        """每个「可成熟」的原语都必须有造帧路径，否则成熟了也回放不出来。"""
        from crypto_strategy.replay import METRIC_BACKED_FIELDS, REPLAY_FIELD_PATHS
        assert set(METRIC_BACKED_FIELDS) <= set(REPLAY_FIELD_PATHS)

    def test_matured_field_actually_gates_trades(self, monkeypatch):
        """funding_rate 成熟后，它真的挡掉不满足的交易日（不是只改文案）。"""
        from crypto_strategy import backtest_gate as bg
        from crypto_strategy.replay import build_frames, dsl_on_bar

        spec = _spec()
        # `_bars` 的日期是 i%28 循环的（会撞键），逐日回放必须用唯一日期
        bars = [{**b, "date": f"2026-{(i // 28) + 1:02d}-{i % 28 + 1:02d}"}
                for i, b in enumerate(_bars(60))]
        dates = [b["date"] for b in bars]
        # 只有第 30 天 funding 为负（满足 entry `funding_rate < 0`），其余为正
        maps = {"funding_rate": {d: (-0.001 if i == 30 else 0.001)
                                 for i, d in enumerate(dates)}}
        frames = build_frames(dates, maps)
        seen: list[str] = []

        def proxy(history):        # 代理天天喊买，让 DSL 条件成为唯一约束
            return "buy"

        on_bar = dsl_on_bar(spec, frames, {"funding_rate"}, proxy,
                            date_of=lambda idx: str(idx)[:10])
        import pandas as pd
        df = pd.DataFrame(bars).set_index("date")
        for i in range(len(df)):
            if on_bar(df.iloc[:i + 1]) == "buy":
                seen.append(dates[i])
        assert seen == [dates[30]]          # 只有 funding 为负那天放行
        assert bg._non_replayable_fields(spec, {"funding_rate"}) == ["composite"]

    def test_all_matured_drops_proxy_and_clears_degraded(self):
        """规则原语全成熟 → 代理退场、degraded=False（诚实契约真正兑现）。"""
        from crypto_strategy.replay import dsl_on_bar

        spec = _spec(
            entry_rules=EntryRules(when=ConditionGroup(
                all_of=[Condition(field="funding_rate", op="lt", value=0)])),
            exit_rules=ExitRules(when=ConditionGroup(
                all_of=[Condition(field="funding_rate", op="gt", value=0.01)])))

        def proxy(history):
            raise AssertionError("原语全成熟时不该再调用双均线代理")

        import pandas as pd
        df = pd.DataFrame(_bars(60)).set_index("date")
        on_bar = dsl_on_bar(spec, {}, {"funding_rate"}, proxy, date_of=lambda idx: str(idx)[:10])
        assert on_bar(df.iloc[:40]) is None          # 无帧 → 条件不满足，但代理没被碰

        def run_pf(bars_by_symbol, signals_by_symbol, **kw):
            return {"metrics": {"total_return": 0.05}}
        r = run_backtest_gate(spec, {"BTCUSDT.BN": _bars()}, run_pf=run_pf,
                              collect=_fake_collect,
                              _matured={"funding_rate"})
        assert r["degraded"] is False and r["degraded_reasons"] == []
        assert r["replay"] == {"mode": "dsl", "proxy_used": False,
                               "evaluated_fields": ["funding_rate"]}

    def test_filter_drops_unreplayable_and_keeps_semantics(self):
        """过滤只剔非可回放条件；剔空 → None（无约束），不是「判不满足」。"""
        from crypto_strategy.replay import filter_replayable
        group = ConditionGroup(
            all_of=[Condition(field="funding_rate", op="lt", value=0),
                    Condition(field="composite", op="gte", value=60)])
        kept = filter_replayable(group, {"funding_rate"})
        assert [c.field for c in kept.all_of] == ["funding_rate"]
        assert filter_replayable(group, set()) is None


# ── service：编译落库 + arm 武装闸 ──

def test_compile_persist_and_arm(mem_db, monkeypatch):
    from crypto_strategy.service import crypto_strategy_service as svc

    # mock 回测为净正（避开 C++/DataEngine）
    monkeypatch.setattr(svc, "backtest",
                        lambda spec: {"passed": True, "net_return": 0.2,
                                      "metrics": {}, "degraded": True, "degraded_reasons": [],
                                      "per_symbol": []})
    res = svc.compile_and_persist(_spec(), description_nl="牛市抄底")
    sid = res["strategy_id"]
    assert res["summary"]["status"] == "backtested"
    assert res["summary"]["backtest_passed"] is True

    armed = svc.arm(sid)
    assert armed["mode"] == "live" and armed["enabled"] is True and armed["status"] == "armed"


def test_arm_no_longer_needs_backtest(mem_db, monkeypatch):
    """半自动系统：arm 不卡回测（安全靠逐笔确认+护栏+风控）。回测负也能上实盘。"""
    from crypto_strategy.service import crypto_strategy_service as svc

    monkeypatch.setattr(svc, "backtest",
                        lambda spec: {"passed": False, "net_return": -0.1,
                                      "metrics": {}, "degraded": True, "degraded_reasons": [],
                                      "per_symbol": []})
    res = svc.compile_and_persist(_spec())
    armed = svc.arm(res["strategy_id"])       # 回测未过也 arm 成功
    assert armed["mode"] == "live" and armed["enabled"] is True and armed["status"] == "armed"


def test_paper_enable_needs_no_backtest(mem_db, monkeypatch):
    from crypto_strategy.service import crypto_strategy_service as svc
    monkeypatch.setattr(svc, "backtest",
                        lambda spec: {"passed": False, "net_return": -0.1, "metrics": {},
                                      "degraded": True, "degraded_reasons": [], "per_symbol": []})
    res = svc.compile_and_persist(_spec())
    enabled = svc.enable_paper(res["strategy_id"])   # 纸面不需回测通过
    assert enabled["mode"] == "paper" and enabled["enabled"] is True


def test_spec_roundtrip_through_db(mem_db, monkeypatch):
    from crypto_strategy.service import crypto_strategy_service as svc
    from crypto_strategy.service import spec_from_row
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy
    monkeypatch.setattr(svc, "backtest", lambda spec: {"passed": True, "net_return": 0.1,
                        "metrics": {}, "degraded": True, "degraded_reasons": [], "per_symbol": []})
    res = svc.compile_and_persist(_spec(strategy_kind="arb",
                                        cost_model=CostModel(min_net_edge_pct=0.01)))
    s = get_session()
    try:
        row = s.query(CryptoStrategy).filter_by(strategy_id=res["strategy_id"]).first()
        rebuilt = spec_from_row(row)
        assert rebuilt.strategy_kind == "arb"
        assert rebuilt.cost_model.min_net_edge_pct == 0.01
        assert rebuilt.universe.symbols == ["BTCUSDT.BN"]
    finally:
        s.close()
