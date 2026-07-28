"""竞技场四道门槛（S3）。

**验收要求：每一道门槛都有单独会红的用例** —— 只测「全过」和「全不过」是在测空气：
一道门槛写反了，那两个用例照样绿。

四个必须被钉死的坑（`00-PLAN §3` 裁决 9，量化经典送命题）：

1. paper 与 live 不可比 → 判「不可比」，⛔ 不是判挑战者赢
2. 多重比较 → 挑战者越多，线越高
3. 追逐近期最优 → 冷却期 + 观察期一起挡
4. 收益率是错的指标 → 比显著性（自带波动惩罚）+ 单独卡回撤
"""
import math

import pytest

from crypto_strategy import arena


def _ret(mean: float, n: int = 40, wobble: float = 0.001) -> list[float]:
    """造一条日收益率序列：均值 mean，带一点确定性的锯齿波动（不能全平，否则 se=0）。"""
    return [mean + (wobble if i % 2 else -wobble) for i in range(n)]


def _side(basis="live_fills", returns=None, days=40, trade_days=30,
          backtest=True, benchmark=False, **kw):
    return {"strategy_id": "CS-X", "name": "策略", "version": 1,
            "basis": basis, "returns": returns if returns is not None else _ret(0.0),
            "days": days, "trade_days": trade_days,
            "backtest_passed": backtest, "is_benchmark": benchmark, **kw}


def _both_pass():
    """一组「四道门槛全过」的输入，各用例在它上面只改一处 —— 这样红的原因唯一。"""
    ch = _side(returns=_ret(0.004))          # 挑战者日均 +0.4%
    mp = _side(returns=_ret(0.0))            # 卫冕者日均 0
    return ch, mp


def test_baseline_all_gates_pass():
    ch, mp = _both_pass()
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is True, r["gates"]
    assert r["blocked_by"] == []


# ── 每道门槛单独会红 ──────────────────────────────────────────────────────

def test_gate_comparable_blocks_paper_vs_live():
    """🔴 坑 1：paper 是理想撮合，跟 live 比大小是自欺 —— 判「不可比」不是判挑战者赢。"""
    ch, mp = _both_pass()
    ch["basis"] = "paper_simulated"
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is False
    assert r["blocked_by"][0] == "comparable"
    assert "不可比" in r["gates"]["comparable"]["detail"]
    assert "理想撮合" in r["gates"]["comparable"]["detail"]


@pytest.mark.parametrize("days,tdays", [(29, 30), (40, 19)])
def test_gate_observation_blocks_short_samples(days, tdays):
    """样本不够就换 = 赌运气。天数和交易日数**任一**不够都要拦。"""
    ch, mp = _both_pass()
    ch["days"], ch["trade_days"] = days, tdays
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is False
    assert "observation" in r["blocked_by"]


def test_gate_significance_blocks_noise_level_lead():
    """🔴 坑 3：领先幅度还在噪声里就换，正是「追逐近期最优」翻车的地方。"""
    ch, mp = _both_pass()
    ch["returns"] = _ret(0.0001)             # 只领先一丁点
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is False
    assert "significance" in r["blocked_by"]


def test_gate_backtest_blocks():
    ch, mp = _both_pass()
    ch["backtest_passed"] = False
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is False and "backtest" in r["blocked_by"]


def test_gate_risk_blocks_higher_drawdown():
    """🔴 坑 4：赚得多但波动更大，**这不算赢**。"""
    ch, mp = _both_pass()
    # 挑战者均值更高，但中间挖一个大坑 → 回撤远超卫冕者
    ch["returns"] = [0.05] * 10 + [-0.25] * 3 + [0.05] * 27
    mp["returns"] = _ret(0.0)
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is False
    assert "risk" in r["blocked_by"]
    assert r["gates"]["risk"]["challenger_max_drawdown"] > \
        r["gates"]["risk"]["champion_max_drawdown"]


def test_gate_cooldown_blocks_even_when_everything_else_passes():
    """🔴 坑 3 的另一半：刚换过就再换 = 反复横跳吃双份手续费。"""
    ch, mp = _both_pass()
    r = arena.evaluate_challenger(ch, mp, days_since_last_switch=3)
    assert r["should_switch"] is False
    assert r["blocked_by"] == ["cooldown"]
    assert "冷却期" in r["verdict"] or "换过" in r["verdict"]


def test_cooldown_is_not_applied_when_never_switched():
    ch, mp = _both_pass()
    r = arena.evaluate_challenger(ch, mp, days_since_last_switch=None)
    assert r["gates"]["cooldown"]["passed"] is True
    assert "没切换过" in r["gates"]["cooldown"]["detail"]


# ── 坑 2：多重比较 ───────────────────────────────────────────────────────

def test_more_challengers_raise_the_bar():
    """🔴 同时跑 10 条，最好的那条大概率是运气 —— 挑战者越多，线必须越高。"""
    ch, mp = _both_pass()
    one = arena.significance(ch["returns"], mp["returns"], challenger_count=1)
    ten = arena.significance(ch["returns"], mp["returns"], challenger_count=10)
    assert ten["threshold"] > one["threshold"]
    assert ten["se_multiple_effective"] > one["se_multiple_effective"]


def test_a_marginal_lead_survives_one_challenger_but_not_ten():
    """线抬高必须**真的能改变结论**，不然那个修正只是装饰。"""
    mp_r = _ret(0.0)
    # 找一个恰好只在 N=1 时显著的均值
    se = math.sqrt(arena._var(mp_r) / len(mp_r) * 2)
    ch_r = _ret(arena.SE_MULTIPLE * se * 1.2)
    assert arena.significance(ch_r, mp_r, challenger_count=1)["passed"] is True
    assert arena.significance(ch_r, mp_r, challenger_count=10)["passed"] is False


def test_significance_needs_variance():
    """两条都毫无波动 → 谈不上显著，不能因为「差值 > 0」就放行。"""
    r = arena.significance([0.01] * 10, [0.0] * 10)
    assert r["passed"] is False and "没有波动" in r["reason"]


def test_significance_needs_enough_points():
    r = arena.significance([0.01], [0.0])
    assert r["passed"] is False and "样本点不足" in r["reason"]


# ── 裁决 8：基准线不参与切换 ─────────────────────────────────────────────

def test_benchmark_never_takes_over():
    """⭐ 基准线是尺子不是选手 —— 哪怕它样样都赢，也不上位。"""
    ch, mp = _both_pass()
    ch["is_benchmark"] = True
    ch["returns"] = _ret(0.5)                # 赢麻了
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is False
    assert "基准线" in r["verdict"] and "不参与切换" in r["verdict"]


# ── 最大回撤 ─────────────────────────────────────────────────────────────

def test_max_drawdown_uses_compounding_not_sum():
    """⚠️ 收益率是比例：-50% 之后 +50% 回不到原点。累加会把这件事抹平。"""
    assert arena.max_drawdown([-0.5, 0.5]) == pytest.approx(0.5, abs=1e-6)
    assert arena.max_drawdown([]) == 0.0
    assert arena.max_drawdown([0.01] * 10) == 0.0       # 一路向上没有回撤


# ── 裁决 11：卫冕者变弱是独立触发 ────────────────────────────────────────

def test_champion_weakening_detects_a_real_drop():
    returns = _ret(0.01, n=40) + _ret(-0.01, n=14)
    r = arena.champion_weakening(returns)
    assert r["weakening"] is True
    assert "超噪声" in r["reason"]
    # ⛔ 只报警不自动降级 —— 自动降级 = 规则能停掉 Jason 的策略，越权
    assert "不自动降级" in r["note"]


def test_champion_weakening_ignores_noise():
    r = arena.champion_weakening(_ret(0.01, n=54))
    assert r["weakening"] is False


def test_champion_weakening_needs_history():
    r = arena.champion_weakening(_ret(0.01, n=10))
    assert r["weakening"] is False and "历史不足" in r["reason"]


# ── verdict 是人话且给动作 ───────────────────────────────────────────────

def test_verdict_names_the_blocking_gate_and_says_what_to_do():
    ch, mp = _both_pass()
    ch["backtest_passed"] = False
    v = arena.evaluate_challenger(ch, mp)["verdict"]
    assert "不换" in v and "回测过闸" in v
    assert "先让它过净费回测" in v


def test_verdict_when_switching_still_warns():
    """规则说可以换，也要提醒它不知道行情背景（裁决 10：AI 可以对规则提异议）。"""
    ch, mp = _both_pass()
    v = arena.evaluate_challenger(ch, mp)["verdict"]
    assert "可以换" in v
    assert "别换" in v or "先小仓位" in v


# ── 组装层：从库里取数 → 判定 ────────────────────────────────────────────

_SPEC = {
    "name": "测试策略",
    "universe": {"symbols": ["BTCUSDT.BN"]},
    "entry_rules": {"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
    "exit_rules": {"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}},
    "guardrails": {"per_order_notional_usdt": 100, "max_orders_per_day": 3},
}


@pytest.fixture
def db():
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
    yield
    s = get_session()
    try:
        for m in (CryptoStrategyRun, CryptoPendingOrder, CryptoStrategyProposal,
                  CryptoArenaSwitch, CryptoTrade, CryptoFill, CryptoStrategy):
            s.query(m).delete()
        s.commit()
    finally:
        s.close()


def _mk(name="策略", **over):
    import copy

    from crypto_intel_engine.dsl import CryptoStrategySpec
    from crypto_strategy.service import crypto_strategy_service as svc
    spec = copy.deepcopy(_SPEC)
    spec["name"] = name
    r = svc.compile_and_persist(CryptoStrategySpec(**spec), do_backtest=False)
    if over:
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategy
        s = get_session()
        try:
            row = s.query(CryptoStrategy).filter(
                CryptoStrategy.strategy_id == r["strategy_id"]).first()
            for k, v in over.items():
                setattr(row, k, v)
            s.commit()
        finally:
            s.close()
    return r["strategy_id"]


def test_arena_without_a_champion_says_so(db):
    _mk("只有纸面的", enabled=1, mode="paper")
    r = arena.evaluate_arena()
    assert r["champion"] is None
    assert "没有卫冕者" in r["verdict"] and "arm 一条" in r["verdict"]


def test_arena_reports_no_challengers(db):
    _mk("卫冕者", enabled=1, mode="live", status="armed")
    r = arena.evaluate_arena()
    assert r["champion"] is not None
    assert r["challengers"] == 0
    assert "没有挑战者" in r["verdict"]


def test_arena_lists_benchmarks_separately(db):
    _mk("卫冕者", enabled=1, mode="live", status="armed")
    _mk("Jason 手写双均线", enabled=1, mode="paper", is_benchmark=1)
    r = arena.evaluate_arena()
    # ⭐ 基准线单列，不算进挑战者数（不然它还会抬高多重比较的门槛）
    assert r["challengers"] == 0
    assert [b["name"] for b in r["benchmarks"]] == ["Jason 手写双均线"]


def test_arena_never_writes_to_db(db):
    """判定只是意见 —— 它**不写库、不改状态**，真要换仍要走 arm 的确认门。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoArenaSwitch, CryptoStrategy
    sid = _mk("卫冕者", enabled=1, mode="live", status="armed")
    _mk("挑战者", enabled=1, mode="paper")
    arena.evaluate_arena()
    s = get_session()
    try:
        assert s.query(CryptoArenaSwitch).count() == 0
        row = s.query(CryptoStrategy).filter(CryptoStrategy.strategy_id == sid).first()
        assert row.status == "armed" and row.enabled == 1
    finally:
        s.close()


def test_arm_records_a_switch_only_when_it_replaces_someone(db):
    """🔴 冷却期靠切换留痕算 —— 但**首次 arm 一条全新策略不算切换**。

    记进去的话冷却期从第一天就开始倒计时，「刚建好想调一下」也会被挡掉。
    """
    from crypto_intel_engine.dsl import CryptoStrategySpec
    from crypto_strategy.service import crypto_strategy_service as svc
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoArenaSwitch

    a = _mk("甲")
    svc.arm(a)
    s = get_session()
    try:
        assert s.query(CryptoArenaSwitch).count() == 0, "首次 arm 被当成了切换"
    finally:
        s.close()

    import copy
    spec = copy.deepcopy(_SPEC)
    spec["interval_minutes"] = 15
    f = svc.fork_version(a, CryptoStrategySpec(**spec), do_backtest=False)
    svc.arm(f["strategy_id"])
    s = get_session()
    try:
        rows = s.query(CryptoArenaSwitch).all()
        assert len(rows) == 1
        assert rows[0].from_strategy_id == a
        assert rows[0].to_strategy_id == f["strategy_id"]
    finally:
        s.close()


# ── 复审补的回归 ─────────────────────────────────────────────────────────

def test_max_drawdown_really_distinguishes_compounding_from_sum():
    """🔴 原用例是假测试：把 `equity *= (1+r)` 改成 `equity += r`，25 条全绿。

    `[-0.5, 0.5]` 累乘/累加都得 0.5、`[0.01]*10` 都得 0 —— 区分不出来。
    `[0.5, -0.5]` 才行：累乘 0.5、累加 0.333。
    """
    assert arena.max_drawdown([0.5, -0.5]) == pytest.approx(0.5, abs=1e-6)


def test_max_drawdown_caps_at_total_loss():
    """r <= -1 之后权益变 0/负数，`(peak-equity)/peak` 就没意义了（实测 [-2.0] → 200%）。"""
    assert arena.max_drawdown([-1.0]) == 1.0
    assert arena.max_drawdown([-2.0]) == 1.0
    assert arena.max_drawdown([0.1, -3.0, 5.0]) == 1.0


def test_observation_gate_counts_trades_not_trade_days():
    """🔴 「≥20 笔」不能实现成「≥20 个有已实现盈亏的自然日」。

    持仓跨日在 crypto 完全正常，按日数会让这道门槛事实上不可达。
    """
    ch, mp = _both_pass()
    ch["trade_count"], ch["trade_days"] = 25, 3     # 25 笔，但只落在 3 天里
    r = arena.evaluate_challenger(ch, mp)
    assert r["gates"]["observation"]["passed"] is True
    assert "成交 25 笔" in r["gates"]["observation"]["detail"]


def test_comparable_gate_blocks_different_capital_basis():
    """🔴 分母不同源 → 收益率整体偏一个倍数，跟策略好坏无关。"""
    ch, mp = _both_pass()
    ch["capital_basis"], mp["capital_basis"] = "real_total_value", "config"
    r = arena.evaluate_challenger(ch, mp)
    assert r["should_switch"] is False
    assert r["blocked_by"][0] == "comparable"
    assert "本金口径不同" in r["gates"]["comparable"]["detail"]


def test_comparable_gate_blocks_when_either_side_has_no_data():
    """两边都是 none 时不能判「两边都是 none，可比」——那句话是错的。"""
    ch, mp = _both_pass()
    ch["basis"] = mp["basis"] = "none"
    r = arena.evaluate_challenger(ch, mp)
    assert r["gates"]["comparable"]["passed"] is False
    assert "算不出收益序列" in r["gates"]["comparable"]["detail"]


def test_risk_gate_surfaces_the_unrealized_blind_spot():
    """🔴 门槛④ 只看得见已实现盈亏 —— 有未平仓头寸时必须说出来，别假装看得见浮亏。

    不说的话，一条「赢了就跑、亏了死扛」的策略在这道门槛上永远干干净净。
    """
    ch, mp = _both_pass()
    ch["open_positions"] = {"BTCUSDT.BN": {"quantity": 1.0}}
    r = arena.evaluate_challenger(ch, mp)
    g = r["gates"]["risk"]
    assert g["unrealized_blind_spot"] is True
    assert g["challenger_open_positions"] == 1
    assert "浮亏不在里面" in g["detail"] and "死扛" in g["detail"]


def test_champion_weakening_ignores_floating_point_noise():
    """与 `significance` 同一把尺子：`> 0` 会让 0.000001% 的下滑被报成变弱。"""
    r = arena.champion_weakening([0.01] * 40 + [0.00999999] * 14)
    assert r["weakening"] is False


def test_champion_weakening_rejects_zero_window():
    """Python 切片陷阱：`returns[:-0]` 是 `[]` 不是「全部」。"""
    r = arena.champion_weakening(_ret(0.01, n=60), recent_days=0)
    assert r["weakening"] is False and "≥1" in r["reason"]


def test_halted_champion_is_still_the_champion(db):
    """🔴 卫冕者一被护栏熔断（enabled 置 0），工具不能说「没有卫冕者」。

    那是**最该给判据的一刻**，而第一版在那时连 champion_weakening 都不算了 ——
    直接顶掉裁决 11「卫冕者变弱更紧急」。
    """
    sid = _mk("卫冕者", enabled=0, mode="live", status="paused_by_guardrail")
    r = arena.evaluate_arena()
    assert r["champion"] is not None
    assert r["champion"]["strategy_id"] == sid
    assert r["champion_halted"] is True
    assert "熔断停机" in r["verdict"]


def test_benchmark_can_be_the_champion(db):
    """🔴 Jason 手写那条如果正在跑实盘（最可能的情形），不能变成「没有卫冕者」。

    裁决 8 说的是基准线不当**挑战者**，没说它不能在跑。
    """
    sid = _mk("Jason 手写双均线", enabled=1, mode="live", status="armed", is_benchmark=1)
    r = arena.evaluate_arena()
    assert r["champion"]["strategy_id"] == sid
    assert r["champion_is_benchmark"] is True
    assert "基准线策略" in r["verdict"]


def test_challengers_key_is_always_an_int(db):
    """🔴 有卫冕者时是 int、没有时是 list → 任何 `len()` 或 `> 0` 的写法必炸其一。"""
    r1 = arena.evaluate_arena()                       # 空库，没有卫冕者
    assert isinstance(r1["challengers"], int)
    _mk("卫冕者", enabled=1, mode="live", status="armed")
    r2 = arena.evaluate_arena()
    assert isinstance(r2["challengers"], int)


def test_arena_output_never_leaks_the_returns_array(db):
    """摘要里不许带 90+ 个 float × N 条 —— 纯烧 token，对「该不该换」零解释力。"""
    import json
    _mk("卫冕者", enabled=1, mode="live", status="armed")
    _mk("挑战者", enabled=1, mode="paper")
    r = arena.evaluate_arena()
    assert "returns" not in json.dumps(r, default=str)
