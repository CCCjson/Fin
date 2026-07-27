"""策略战绩计算（S1）—— 归因正确性 / paper 与 live 分家 / 空数据不炸。

三条最要紧的不变式，每条都对应一个「不做就会说错话」的场景：

1. **别人的成交不能算进这条策略** —— 卫冕者、Jason 手动下的单、来源不明的存量成交，
   全混在同一个币安账户里。算错了就是给策略发一张假奖状，而这张奖状要拿来决定
   切不切换策略。
2. **paper 的数字必须自带「我是模拟的」标签** —— paper 是理想撮合（挂单必成、零冲击），
   和 live 放在一起比大小是量化经典送命题。
3. **「没跑过」≠「跑了但没赚」** —— 前者该去看策略是不是压根没武装，后者该去看规则。
   压成同一句「没有数据」，Jason 就会查错方向。
"""
from datetime import datetime, timedelta

import pytest

from common.market import CRYPTO
from common.market_time import market_day_of
from common.trade_source import AI_ADVICE, STRATEGY, UNKNOWN
from crypto_strategy import performance as perf
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    CryptoFill,
    CryptoStrategy,
    CryptoStrategyRun,
    CryptoTrade,
)

SID = "CS-TEST-0001"
OTHER = "CS-TEST-0002"


def _add(session, obj):
    session.add(obj)
    return obj


@pytest.fixture
def db():
    """每个用例自己造数据，测完清干净（用测试库，见根级 conftest）。"""
    session = get_session()
    yield session
    session.close()
    s = get_session()
    try:
        for model in (CryptoStrategyRun, CryptoTrade, CryptoFill, CryptoStrategy):
            s.query(model).delete()
        s.commit()
    finally:
        s.close()


def _mk_strategy(session, sid=SID, *, mode="live", name="测试策略", **kw):
    row = _add(session, CryptoStrategy(strategy_id=sid, name=name, mode=mode,
                                       status=kw.pop("status", "armed"),
                                       enabled=kw.pop("enabled", 1),
                                       cost_model=kw.pop("cost_model", None), **kw))
    session.commit()
    return row


def _mk_run(session, sid=SID, *, mode="live", status="evaluated", detail=None,
            minutes_ago=10, orders=0):
    import json
    _add(session, CryptoStrategyRun(
        strategy_id=sid, status=status, mode=mode, orders_placed=orders,
        decision_detail=json.dumps(detail) if detail else None,
        started_at=datetime.utcnow() - timedelta(minutes=minutes_ago)))
    session.commit()


def _mk_trade(session, *, order_id, kind, ref=None, symbol="BTCUSDT.BN", side="BUY",
              days_ago=0):
    # ⚠️ **crypto 市场日（UTC）**，不是 `date.today()`（服务器本地日）。北京时间
    # 00:00-08:00 两者差一天，用本地日造数据会让带窗口的断言随时间 flaky。
    day = market_day_of(datetime.utcnow() - timedelta(days=days_ago), CRYPTO)
    _add(session, CryptoTrade(symbol=symbol, side=side, price=100.0, quantity=1.0,
                              amount=100.0, commission=0.0, order_id=order_id,
                              trade_date=day, source_kind=kind, source_ref=ref))
    session.commit()


def _mk_fill(session, *, order_id, trade_id, symbol, price, qty, is_buyer, minutes_ago=5):
    _add(session, CryptoFill(
        source="spot", symbol=symbol, trade_id=trade_id, order_id=order_id,
        price=price, quantity=qty, quote_qty=price * qty, commission=0.0,
        commission_asset="USDT", is_buyer=1 if is_buyer else 0,
        trade_time=datetime.utcnow() - timedelta(minutes=minutes_ago)))
    session.commit()


# ── 1. live 归因 ──────────────────────────────────────────────────────────

def test_live_pnl_only_counts_this_strategys_orders(db):
    """核心不变式：别人的成交一分钱都不能算进来。"""
    _mk_strategy(db, SID, mode="live")
    # 这条策略：100 买入 → 120 卖出，赚 20
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL")
    _mk_fill(db, order_id="A1", trade_id="1", symbol="BTCUSDT.BN",
             price=100.0, qty=1.0, is_buyer=True, minutes_ago=20)
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=120.0, qty=1.0, is_buyer=False, minutes_ago=10)
    # 另一条策略 + 一笔 MoneyBill 下的单 + 一笔来源不明：**都不该被算进去**。
    # ⚠️ 干扰单的价格**必须与本策略不同**（这里 10 而不是 100）：同价的话混进来之后
    # 加权均价不变，`realized_pnl` 照样是 20 —— 断言看起来通过，其实没在守归因。
    _mk_trade(db, order_id="B1", kind=STRATEGY, ref=OTHER)
    _mk_fill(db, order_id="B1", trade_id="3", symbol="BTCUSDT.BN",
             price=10.0, qty=5.0, is_buyer=True, minutes_ago=20)
    _mk_trade(db, order_id="C1", kind=AI_ADVICE)
    _mk_trade(db, order_id="D1", kind=UNKNOWN)

    r = perf.strategy_pnl(SID)
    assert r["basis"] == "live_fills"
    assert r["realized_pnl"] == pytest.approx(20.0)
    assert r["closed_legs"] == 1
    assert r["coverage"]["attributed_trades"] == 2
    # 诚实度：来源不明的那笔要被报出来，而不是悄悄忽略
    assert r["coverage"]["unknown_source_trades"] == 1


def test_buy_outside_window_still_provides_cost_basis(db):
    """🔴 回归：买在窗口外、卖在窗口内 → 盈亏**不能变成 0**。

    第一版按 `trade_date` 切 order_ids，于是买入腿根本不进回放，
    `costed = min(sold, 0) = 0` → 不记平仓 → 静默报「赚了 0」。而 `days=30` 是
    工具默认值、crypto 持仓跨月完全正常 —— 这是常态不是边角。
    成本必须从全历史累，窗口只切在平仓日上。
    """
    _mk_strategy(db, SID, mode="live")
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID, days_ago=40)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL", days_ago=0)
    _mk_fill(db, order_id="A1", trade_id="1", symbol="BTCUSDT.BN",
             price=100.0, qty=1.0, is_buyer=True, minutes_ago=60 * 24 * 40)
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=120.0, qty=1.0, is_buyer=False, minutes_ago=5)

    r = perf.strategy_pnl(SID, since=datetime.utcnow() - timedelta(days=30),
                          until=datetime.utcnow())
    assert r["realized_pnl"] == pytest.approx(20.0), "窗口把买入腿砍掉了"
    assert r["closed_legs"] == 1
    assert r["has_uncosted_sell"] is False


def test_window_still_excludes_old_closes(db):
    """反方向：窗口**要**把窗口外的平仓挡在外面，别把上面那条改成「窗口失效」。"""
    _mk_strategy(db, SID, mode="live")
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID, days_ago=60)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL", days_ago=45)
    _mk_fill(db, order_id="A1", trade_id="1", symbol="BTCUSDT.BN",
             price=100.0, qty=1.0, is_buyer=True, minutes_ago=60 * 24 * 60)
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=120.0, qty=1.0, is_buyer=False, minutes_ago=60 * 24 * 45)

    r = perf.strategy_pnl(SID, since=datetime.utcnow() - timedelta(days=30),
                          until=datetime.utcnow())
    assert r["realized_pnl"] == 0 and r["closed_legs"] == 0


def test_unsynced_fills_is_not_reported_as_zero_profit(db):
    """🔴 回归：「算不出来」不许报成「赚了 0」。

    `crypto_fills` 靠定时同步，成交刚落台账、明细还没拉下来时 `replay()` 返回空。
    此时宣布 `realized_pnl=0` 与「真的平进平出」**完全无法区分**。
    """
    _mk_strategy(db, SID, mode="live")
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL")
    # 刻意不造 CryptoFill

    r = perf.strategy_pnl(SID)
    assert r["basis"] == "none"
    assert "还没同步" in r["reason"]
    assert r["coverage"]["attributed_trades"] == 2
    assert "realized_pnl" not in r


def test_live_pnl_survives_switching_back_to_paper(db):
    """🔴 回归：切一次 `enable_paper`，真金白银的战绩不许人间蒸发。

    第一版按 `row.mode`（**当前**模式）分支，而 paper→arm→退回 paper 是设计好的
    主流程（裁决 7）。分桶依据必须是**数据本身**。
    """
    _mk_strategy(db, SID, mode="paper")          # 当前是 paper
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL")
    _mk_fill(db, order_id="A1", trade_id="1", symbol="BTCUSDT.BN",
             price=100.0, qty=1.0, is_buyer=True, minutes_ago=60)
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=150.0, qty=1.0, is_buyer=False, minutes_ago=30)

    r = perf.strategy_pnl(SID)
    assert r["basis"] == "live_fills"
    assert r["realized_pnl"] == pytest.approx(50.0)
    assert r["current_mode"] == "paper"           # 当前模式仍如实报出


def test_mixed_blocks_are_never_added_together(db):
    """既有实盘成交又有纸面记录 → 分块返回，且明说不可相加。"""
    _mk_strategy(db, SID, mode="paper", cost_model='{"taker_fee_pct": 0.001}')
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL")
    _mk_fill(db, order_id="A1", trade_id="1", symbol="BTCUSDT.BN",
             price=100.0, qty=1.0, is_buyer=True, minutes_ago=60)
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=150.0, qty=1.0, is_buyer=False, minutes_ago=30)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("BUY", 1.0, 100.0), minutes_ago=90)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("SELL", 1.0, 110.0), minutes_ago=45)

    r = perf.strategy_pnl(SID)
    assert r["basis"] == "mixed"
    assert r["live"]["realized_pnl"] == pytest.approx(50.0)
    assert r["paper"]["realized_pnl"] == pytest.approx(10 - 0.1 - 0.11, abs=1e-6)
    assert "不可相加" in r["note"]
    assert "realized_pnl" not in r      # 顶层不给合计数，防有人直接读它


def test_live_pnl_none_when_no_attributed_trades(db):
    _mk_strategy(db, SID, mode="live")
    _mk_trade(db, order_id="C1", kind=AI_ADVICE)      # 别人的单
    r = perf.strategy_pnl(SID)
    assert r["basis"] == "none"
    assert "没有归属它的成交" in r["reason"]


def test_unknown_strategy_is_not_guessed(db):
    r = perf.strategy_pnl("CS-不存在")
    assert r["basis"] == "none" and "没有" in r["reason"]


# ── 2. paper 分家 ─────────────────────────────────────────────────────────

def _paper_detail(action, qty, price, symbol="BTCUSDT.BN"):
    return {"decisions": [{"symbol": symbol, "status": "ready", "action": action,
                           "qty": qty, "price": price}]}


def test_paper_pnl_is_labelled_simulated(db):
    _mk_strategy(db, SID, mode="paper", cost_model='{"taker_fee_pct": 0.001}')
    _mk_run(db, SID, mode="paper", status="evaluated",
            detail=_paper_detail("BUY", 1.0, 100.0), minutes_ago=60)
    _mk_run(db, SID, mode="paper", status="evaluated",
            detail=_paper_detail("SELL", 1.0, 120.0), minutes_ago=30)

    r = perf.strategy_pnl(SID)
    assert r["basis"] == "paper_simulated"
    assert r["simulated_legs"] == 2
    assert r["closed_legs"] == 1
    # 毛利 20，减两腿手续费（0.1 + 0.12）
    assert r["realized_pnl"] == pytest.approx(20 - 0.1 - 0.12, abs=1e-6)
    assert "不可与 live" in r["note"]


def test_paper_fee_is_not_silently_zero(db):
    """cost_model 读不到时用兜底费率，**不能当 0** —— paper 已经因理想撮合被高估了。"""
    _mk_strategy(db, SID, mode="paper", cost_model=None)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("BUY", 1.0, 100.0), minutes_ago=60)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("SELL", 1.0, 100.0), minutes_ago=30)
    r = perf.strategy_pnl(SID)
    assert r["fees"] > 0
    assert r["realized_pnl"] < 0        # 平进平出应该是亏手续费


def test_paper_ignores_non_ready_decisions(db):
    """`blocked_cost` 之类的决策不是「本应成交」，不许进模拟账。"""
    _mk_strategy(db, SID, mode="paper")
    _mk_run(db, SID, mode="paper", detail={"decisions": [
        {"symbol": "BTCUSDT.BN", "status": "blocked_cost", "action": "BUY",
         "qty": 1.0, "price": 100.0},
        {"symbol": "BTCUSDT.BN", "status": "no_signal"},
    ]})
    assert perf.strategy_pnl(SID)["basis"] == "none"


def test_paper_sell_without_known_buy_is_flagged(db):
    """卖得比已知买入多 → 成本未知，**不按 0 成本算成暴利**（同 cost_basis 口径）。"""
    _mk_strategy(db, SID, mode="paper")
    _mk_run(db, SID, mode="paper", detail=_paper_detail("SELL", 1.0, 100.0))
    r = perf.strategy_pnl(SID)
    assert r["has_uncosted_sell"] is True
    assert r["closed_legs"] == 0
    assert r["realized_pnl"] == 0


# ── 3. 体检报告 ───────────────────────────────────────────────────────────

def test_health_says_never_ran_not_zero(db):
    """「没跑过」≠「跑了但没赚」—— 两者的下一步动作完全不同。"""
    _mk_strategy(db, SID, mode="live", enabled=0, status="draft")
    h = perf.strategy_health(SID)
    assert h["ok"] is False
    assert "一条运行记录都没有" in h["reason"]
    assert "enabled=0" in h["reason"]        # 直接指出该去查什么


def test_health_ranks_no_order_reasons_and_explains(db):
    _mk_strategy(db, SID, mode="live")
    for i in range(4):
        _mk_run(db, SID, mode="live", detail={"decisions": [
            {"symbol": "BTCUSDT.BN", "status": "blocked_cost",
             "reason": "净边际 0.1% 低于门槛 0.5%"},
        ]}, minutes_ago=60 + i)
    _mk_run(db, SID, mode="live", detail={"decisions": [
        {"symbol": "ETHUSDT.BN", "status": "no_signal"}]}, minutes_ago=10)

    h = perf.strategy_health(SID)
    assert h["ok"] is True
    reasons = h["no_order_reasons"]
    assert list(reasons)[0] == "blocked_cost"          # 按占比排序，主因在最前
    assert reasons["blocked_cost"]["count"] == 4
    assert reasons["blocked_cost"]["example"]          # 带一条具体理由，不是干巴巴的计数
    # 结论必须是人话且给出动作，不是数字复述
    assert "边际太薄" in h["verdict"]
    assert "一单都没开" in h["verdict"]


def test_paper_verdict_does_not_claim_never_triggered(db):
    """🔴 回归：paper 触发过还赚了钱，诊断不许说「条件根本没被触发过」。

    paper 下 `_stage_or_log` 直接 return None → `orders_placed` **恒为 0**，
    拿它当「开了几单」的判据会给出**方向相反**的下一步动作（去放宽阈值，
    而实际上它触发得好好的）。paper 的等价物是 `ready` 决策数。
    """
    _mk_strategy(db, SID, mode="paper", cost_model='{"taker_fee_pct": 0.001}')
    _mk_run(db, SID, mode="paper", detail=_paper_detail("BUY", 1.0, 100.0), minutes_ago=90)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("SELL", 1.0, 130.0), minutes_ago=45)
    _mk_run(db, SID, mode="paper",
            detail={"decisions": [{"symbol": "ETHUSDT.BN", "status": "no_signal"}]},
            minutes_ago=10)

    v = perf.strategy_health(SID)["verdict"]
    assert "一单都没开" not in v
    assert "条件根本没被触发过" not in v
    assert "本应成交" in v          # 说清楚是纸面干跑
    assert "模拟账" in v


def test_verdict_translates_skipped_dup(db):
    """漏 label 会把 raw key（`skipped_dup`）原样念给 Jason。"""
    _mk_strategy(db, SID, mode="live")
    _mk_run(db, SID, mode="live", detail={"decisions": [
        {"symbol": "BTCUSDT.BN", "status": "skipped_dup", "note": "已有同向未决单"}]})
    h = perf.strategy_health(SID)
    assert "skipped_dup" not in h["verdict"]
    assert "同向待确认单" in h["verdict"]
    assert "先去待确认单那儿" in h["verdict"]


def test_verdict_surfaces_uncosted_sell(db):
    """`has_uncosted_sell` 此前从不进 verdict —— 于是「买入腿不在账里」被念成「赚了 0」。"""
    _mk_strategy(db, SID, mode="live")
    _mk_run(db, SID, mode="live", detail={"decisions": [
        {"symbol": "BTCUSDT.BN", "status": "ready", "action": "SELL"}]}, orders=1)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL")
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=120.0, qty=1.0, is_buyer=False, minutes_ago=5)
    h = perf.strategy_health(SID)
    assert h["pnl"]["has_uncosted_sell"] is True
    assert "成本未知" in h["verdict"]


def test_health_flags_halted_strategy(db):
    _mk_strategy(db, SID, mode="live", status="paused_by_guardrail",
                 halted_reason="当日回撤 -3.2% 超阈值", consecutive_guardrail_trips=2)
    _mk_run(db, SID, mode="live", status="blocked_guardrail")
    h = perf.strategy_health(SID)
    assert "熔断停机" in h["verdict"]
    assert h["guardrails"]["consecutive_trips"] == 2


def test_health_window_excludes_old_runs(db):
    """⚠️ `started_at` 是 naive UTC，窗口切错就会把陈年 run 算进「最近 7 天」。"""
    _mk_strategy(db, SID, mode="live")
    _mk_run(db, SID, mode="live", minutes_ago=60 * 24 * 40)     # 40 天前
    assert perf.strategy_health(SID, days=7)["ok"] is False
    assert perf.strategy_health(SID, days=90)["ok"] is True


# ── 4. 总览 ───────────────────────────────────────────────────────────────

def test_standings_lists_all_without_ranking(db):
    _mk_strategy(db, SID, mode="live", name="甲")
    _mk_strategy(db, OTHER, mode="paper", name="乙")
    rows = perf.standings()
    assert {r["strategy_id"] for r in rows} == {SID, OTHER}
    assert all(r["verdict"] for r in rows)      # 每条都要有人话，不能只有数字


def test_standings_on_empty_db_does_not_blow_up(db):
    assert perf.standings() == []
