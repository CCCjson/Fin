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
    StrategyTrade,
    TradingCalendar,
)

SID = "CS-TEST-0001"
OTHER = "CS-TEST-0002"
STOCK_SID = "CS-TEST-STOCK"


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
        for model in (CryptoStrategyRun, CryptoTrade, CryptoFill, StrategyTrade,
                      TradingCalendar, CryptoStrategy):
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


# ── 5. 日收益率序列（S3 四道门槛的地基）────────────────────────────────────

def test_daily_returns_pads_non_trading_days_with_zero(db):
    """🔴 没交易的日子记 0 而**不是缺失**。

    不补齐的话，「30 天里只有 3 天有交易」会被算成「3 个样本点、波动极小、
    显著性爆表」—— 那是量化里最经典的一种自欺，而门槛② 正好吃这个序列。
    """
    _mk_strategy(db, SID, mode="paper", cost_model='{"taker_fee_pct": 0.001}')
    _mk_run(db, SID, mode="paper", detail=_paper_detail("BUY", 1.0, 100.0),
            minutes_ago=60 * 24 * 3)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("SELL", 1.0, 120.0),
            minutes_ago=60 * 24 * 2)

    r = perf.daily_returns(SID, days=10)
    assert r["basis"] == "paper_simulated"
    assert len(r["returns"]) == len(r["dates"]) == r["window_days"]
    assert r["window_days"] >= 10               # 按自然日补齐，不是只有交易日
    assert r["trade_days"] == 1                 # 只有平仓那天非零
    assert sum(1 for x in r["returns"] if x == 0.0) == r["window_days"] - 1
    # 🔴 `days` 是**策略实际活了多久**，不是窗口长度。第一版返回窗口长度，
    # 于是一条今天刚建的策略也显示「跑了 91 天」，门槛① 的天数那一半永久失效。
    assert r["days"] == 0, "days 又变回窗口长度了"


def test_daily_returns_days_is_strategy_age_not_window(db):
    """门槛① 的天数那一半必须真的能拦住新策略。"""
    from datetime import datetime
    from datetime import timedelta as _td
    row = _mk_strategy(db, SID, mode="paper", cost_model='{"taker_fee_pct": 0.001}')
    _mk_run(db, SID, mode="paper", detail=_paper_detail("BUY", 1.0, 100.0), minutes_ago=90)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("SELL", 1.0, 120.0), minutes_ago=45)

    assert perf.daily_returns(SID, days=90)["days"] == 0      # 刚建的
    s = get_session()
    try:
        r2 = s.query(CryptoStrategy).filter(CryptoStrategy.strategy_id == SID).first()
        r2.created_at = datetime.utcnow() - _td(days=45)
        s.commit()
    finally:
        s.close()
    assert perf.daily_returns(SID, days=90)["days"] == 45     # 活了 45 天
    assert perf.daily_returns(SID, days=10)["days"] == 10     # 与窗口取小
    assert row is not None


def test_daily_returns_matches_the_aggregate(db):
    """按日拆开的总和，必须等于 `strategy_pnl` 的汇总值 —— 两处配对规则是同一套。"""
    _mk_strategy(db, SID, mode="paper", cost_model='{"taker_fee_pct": 0.001}')
    _mk_run(db, SID, mode="paper", detail=_paper_detail("BUY", 1.0, 100.0),
            minutes_ago=60 * 24 * 3)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("SELL", 1.0, 120.0),
            minutes_ago=60 * 24 * 2)

    agg = perf.strategy_pnl(SID)["realized_pnl"]
    dr = perf.daily_returns(SID, days=10)
    assert sum(dr["returns"]) * dr["capital"] == pytest.approx(agg, abs=1e-4)


def test_daily_returns_none_when_no_data(db):
    _mk_strategy(db, SID, mode="live")
    r = perf.daily_returns(SID)
    assert r["basis"] == "none" and "没有可算收益" in r["reason"]


def test_daily_returns_uses_live_when_both_exist(db):
    """真金白银优先：两边都有数据时，序列用 live（paper 是理想撮合，掺进来会污染判定）。"""
    _mk_strategy(db, SID, mode="paper", cost_model='{"taker_fee_pct": 0.001}')
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL")
    _mk_fill(db, order_id="A1", trade_id="1", symbol="BTCUSDT.BN",
             price=100.0, qty=1.0, is_buyer=True, minutes_ago=60 * 24 * 2)
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=150.0, qty=1.0, is_buyer=False, minutes_ago=60 * 24)
    _mk_run(db, SID, mode="paper", detail=_paper_detail("BUY", 1.0, 100.0), minutes_ago=90)

    assert perf.daily_returns(SID, days=10)["basis"] == "live_fills"


# ── 6. 股票策略：读的是另一张台账（S5）─────────────────────────────────────
#
# `crypto_strategies` 这张表同时存币策略和股票策略（靠 `market` 列区分），
# 但两者的成交落在**不同的台账**里。这一节守三件事：
#
# 1. 股票策略能算出数（此前 `strategy_pnl` 只认 `crypto_trades` → 恒为 none，
#    于是股票策略进了排行榜却永远显示空白）；
# 2. crypto 那半边**一行行为都没变**（它正在跑真钱）；
# 3. paper 与 live 分桶不合并 —— 股票两种模式落在**同一张表**里，
#    全靠 `mode` 列分，少一个 filter 就是拿模拟成绩给真钱决策背书。

def _mk_stock_fill(session, *, sid=STOCK_SID, market="a_share", symbol="600519.SH",
                   side="BUY", price=100.0, qty=100, mode="live",
                   commission=0.0, days_ago=0, on=None):
    """造一行成交台账。

    ⚠️ `days_ago` 是**按市场当地日**往回数（`market_today`），⛔ 不是 `utcnow().date()`：
    UTC ≥16:00（北京 00:00-08:00）两者差一天，用 UTC 日算会让成交整体落到目标日 +1，
    于是断绝对日期的用例**每天红 8 小时**，而且它测的还是「按当地日切」这件事本身。
    断绝对日期时直接传 `on=date(...)`，别用相对天数。
    """
    from common.market_time import market_today as _mtoday
    day = on if on is not None else _mtoday(market) - timedelta(days=days_ago)
    _add(session, StrategyTrade(
        market=market, symbol=symbol, side=side, price=price, quantity=qty,
        amount=price * qty, commission=commission, trade_date=day,
        source_kind=STRATEGY, source_ref=sid, rule_set="趋势", mode=mode))
    session.commit()


def test_stock_pnl_reads_the_stock_ledger(db):
    """股票策略走 `strategy_trades`，不再恒为「算不出来」。"""
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share", name="茅台策略")
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, commission=3.0, days_ago=3)
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, commission=15.0, days_ago=1)

    r = perf.strategy_pnl(STOCK_SID)
    assert r["basis"] == "live_fills"
    assert r["market"] == "a_share"
    assert r["closed_legs"] == 1
    # 毛利 2000，减两腿手续费（3 + 15）
    assert r["realized_pnl"] == pytest.approx(2000 - 3 - 15, abs=1e-6)
    assert r["fees"] == pytest.approx(18.0, abs=1e-6)


def test_stock_commission_comes_from_the_ledger_row(db):
    """⚠️ 费用取台账里**券商回报的实际手续费**，不是一个估出来的费率。

    平进平出必须是**亏手续费**：抹掉费用的话，一条来回不赚钱的策略
    看起来会是平的，而它真实在慢慢流血。
    """
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, commission=3.0, days_ago=3)
    _mk_stock_fill(db, side="SELL", price=100.0, qty=100, commission=13.0, days_ago=1)
    r = perf.strategy_pnl(STOCK_SID)
    assert r["realized_pnl"] == pytest.approx(-16.0, abs=1e-6)


def test_stock_paper_and_live_are_separate_buckets(db):
    """🔒 两种模式在**同一张表**里 —— 分桶全靠 `mode` 列，⛔ 绝不合并。"""
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_stock_fill(db, mode="live", side="BUY", price=100.0, qty=100, days_ago=3)
    _mk_stock_fill(db, mode="live", side="SELL", price=110.0, qty=100, days_ago=1)
    _mk_stock_fill(db, mode="paper", side="BUY", price=100.0, qty=100, days_ago=3)
    _mk_stock_fill(db, mode="paper", side="SELL", price=200.0, qty=100, days_ago=1)

    r = perf.strategy_pnl(STOCK_SID)
    assert r["basis"] == "mixed"
    assert r["live"]["realized_pnl"] == pytest.approx(1000.0, abs=1e-6)
    assert r["paper"]["realized_pnl"] == pytest.approx(10000.0, abs=1e-6)
    # ⛔ 顶层不许出现一个「合计」，那正是拿模拟成绩给真钱背书的入口
    assert "realized_pnl" not in r
    assert "不可相加" in r["note"]


def test_stock_buy_outside_window_still_provides_cost_basis(db):
    """🔴 回归：买在窗口外、卖在窗口内 → 盈亏**不能变成 0**。

    成本基础天然跨窗口（股票持仓跨月完全正常）。按 `since` 切成交会把买入腿
    切掉 → `costed = min(sold, 0) = 0` → 那笔平仓不记 → 静默报「赚了 0」。
    成本必须从全历史累，窗口只切在平仓日上。
    """
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, days_ago=40)
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, days_ago=5)

    r = perf.strategy_pnl(STOCK_SID, since=datetime.utcnow() - timedelta(days=30),
                          until=datetime.utcnow())
    assert r["closed_legs"] == 1
    assert r["realized_pnl"] == pytest.approx(2000.0, abs=1e-6)


def test_stock_window_excludes_older_closes(db):
    """窗口切在平仓日上：窗口外平的仓不该算进这个窗口的战绩。"""
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, days_ago=50)
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, days_ago=40)

    r = perf.strategy_pnl(STOCK_SID, since=datetime.utcnow() - timedelta(days=30),
                          until=datetime.utcnow())
    assert r["closed_legs"] == 0
    assert r["realized_pnl"] == 0


def test_stock_no_fills_says_never_traded_not_zero(db):
    """「一笔成交都没有」≠「跑了但没赚」—— 两者该查的方向完全不同。"""
    _mk_strategy(db, STOCK_SID, mode="paper", market="us_stock")
    r = perf.strategy_pnl(STOCK_SID)
    assert r["basis"] == "none"
    assert "一笔成交都没有" in r["reason"]


def test_stock_unknown_mode_fills_are_not_silently_dropped(db):
    """⚠️ 悄悄少算一批成交，和「这条策略没赚钱」在屏幕上长得一模一样。"""
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_stock_fill(db, mode="shadow", side="BUY", price=100.0, qty=100, days_ago=2)
    r = perf.strategy_pnl(STOCK_SID)
    assert r["unbucketed_fills"] == {"shadow": 1}


def test_crypto_strategy_ignores_the_stock_ledger(db):
    """🔴 分叉的另一半：币策略**不许**去读 `strategy_trades`。

    存量币策略的 `market` 是 NULL（模型约定 NULL = crypto）。兜底若写成
    `normalize_market` 的默认值（A股），所有老币策略会一夜之间去读一张空的
    股票台账 —— 战绩集体归零，而且一声不吭。
    """
    _mk_strategy(db, SID, mode="live", market=None)
    _mk_trade(db, order_id="A1", kind=STRATEGY, ref=SID)
    _mk_trade(db, order_id="A2", kind=STRATEGY, ref=SID, side="SELL")
    _mk_fill(db, order_id="A1", trade_id="1", symbol="BTCUSDT.BN",
             price=100.0, qty=1.0, is_buyer=True, minutes_ago=20)
    _mk_fill(db, order_id="A2", trade_id="2", symbol="BTCUSDT.BN",
             price=120.0, qty=1.0, is_buyer=False, minutes_ago=10)
    # 同一个 strategy_id 在股票台账里也有行（不该发生，但发生了也不能混进来）
    _mk_stock_fill(db, sid=SID, side="BUY", price=1.0, qty=100, days_ago=3)
    _mk_stock_fill(db, sid=SID, side="SELL", price=999.0, qty=100, days_ago=1)

    r = perf.strategy_pnl(SID)
    assert r["basis"] == "live_fills"
    assert r["realized_pnl"] == pytest.approx(20.0)      # 币的 20，不是股票的 99800
    assert "market" not in r                             # crypto 分支的返回形状没变


def test_stock_pnl_sentence_uses_the_market_currency(db):
    """⚠️ 一条茅台策略的盈亏念成「USDT」是说了句假话，而这句话要拿来做决定。"""
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, days_ago=3)
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, days_ago=1)

    s = perf._pnl_sentence(perf.strategy_pnl(STOCK_SID))
    assert "CNY" in s and "USDT" not in s
    # crypto 不带 currency 字段 → 兜底仍是 USDT（原样，别改成空串）
    assert "USDT" in perf._pnl_sentence({"basis": "live_fills", "realized_pnl": 1.0})


# ── 7. 股票日收益率序列（S5，门槛②④ 的输入）──────────────────────────────
#
# 与 crypto 那条腿的关键差异：**股票有休市日**。拿自然日补零会凭空塞进 ~30% 的零，
# 把 μ 和 σ 一起稀释 —— 而门槛② 正好吃这两个数。

def _mk_calendar(session, market, days):
    for d in days:
        _add(session, TradingCalendar(market=market, cal_date=d, source="manual"))
    session.commit()


def test_stock_series_is_padded_by_trading_days_not_natural_days(db):
    """🔴 休市日不是「策略没交易的一天」，是**这一天不存在**。"""
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    # 造一份只有 5 个交易日的日历（周一到周五），窗口横跨一个周末
    trading = [_date(2026, 7, 20) + timedelta(days=i) for i in range(5)]
    _mk_calendar(db, "a_share", trading)
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100,
                   on=_date(2026, 7, 20))
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100,
                   on=_date(2026, 7, 22))

    r = perf.daily_returns(STOCK_SID,
                           since=datetime(2026, 7, 20), until=datetime(2026, 7, 24, 23))
    assert r["basis"] == "live_fills"
    assert r["calendar_confidence"] == "certain"
    assert r["window_days"] == 5, f"补成了 {r['window_days']} 天，周末被算进来了"
    assert r["dates"] == [d.isoformat() for d in trading]
    assert r["trade_days"] == 1
    assert r["trade_count"] == 1


def test_stock_series_day_is_the_market_local_day(db):
    """⛔ 按日切必须用市场当地日 —— A 股当地日零点在 UTC 是**前一天** 16:00，
    按 UTC 日切整条序列会偏一天，而偏一天的序列在门槛② 眼里完全正常。"""
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    trading = [_date(2026, 7, 20) + timedelta(days=i) for i in range(5)]
    _mk_calendar(db, "a_share", trading)
    sell_day = _date(2026, 7, 22)
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100,
                   on=_date(2026, 7, 20))
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100,
                   on=sell_day)

    r = perf.daily_returns(STOCK_SID,
                           since=datetime(2026, 7, 20), until=datetime(2026, 7, 24, 23))
    hit = [d for d, x in zip(r["dates"], r["returns"], strict=True) if x != 0.0]
    assert hit == [sell_day.isoformat()], f"平仓被记到了 {hit}，不是 {sell_day}"


def test_stock_series_matches_the_aggregate(db):
    """按日拆开的总和必须等于 `strategy_pnl` 的汇总值 —— 两处配对规则是同一套。"""
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_calendar(db, "a_share", [_date(2026, 7, 20) + timedelta(days=i) for i in range(5)])
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, commission=3.0,
                   on=_date(2026, 7, 20))
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, commission=15.0,
                   on=_date(2026, 7, 22))

    since, until = datetime(2026, 7, 20), datetime(2026, 7, 24, 23)
    agg = perf.strategy_pnl(STOCK_SID, since=since, until=until)["realized_pnl"]
    dr = perf.daily_returns(STOCK_SID, since=since, until=until)
    assert sum(dr["returns"]) * dr["capital"] == pytest.approx(agg, abs=1e-4)


def test_stock_fill_outside_the_calendar_is_not_dropped(db):
    """🔴 成交是事实、日历是推断 —— 冲突时以成交为准，并且**说出口**。

    日历自己也可能是残的（拉了一半 / 自举中断）。拿它当唯一判据，就会把真金白银
    赚的钱从序列里悄悄删掉。
    """
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_calendar(db, "a_share", [_date(2026, 7, 20) + timedelta(days=i) for i in range(5)])
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100,
                   on=_date(2026, 7, 20))
    # 7/25 是周六（日历里没有），但台账上就是有一笔平仓
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100,
                   on=_date(2026, 7, 25))

    r = perf.daily_returns(STOCK_SID,
                           since=datetime(2026, 7, 20), until=datetime(2026, 7, 26, 23))
    assert "2026-07-25" in r["dates"], "日历外的成交被从序列里删掉了"
    assert r["off_calendar_days"] == ["2026-07-25"]
    assert sum(r["returns"]) * r["capital"] == pytest.approx(2000.0, abs=1e-4)
    assert "交易日历之外" in r["note"]


def test_stock_series_marks_heuristic_days_as_suspected(db):
    """⚠️ 日历盖不住窗口时要**补齐并降级**，⛔ 不许让序列尾部悄悄少几天。

    `trading_days()` 是给缺口扫描用的，超出覆盖范围时它只回「盖住的那段」——
    对缺口扫描是对的，对收益序列是灾难（少掉的恰恰是最近、最该看的那几天）。
    """
    _mk_strategy(db, STOCK_SID, mode="live", market="us_stock")
    _mk_stock_fill(db, market="us_stock", symbol="AAPL", side="BUY",
                   price=100.0, qty=10, days_ago=6)
    _mk_stock_fill(db, market="us_stock", symbol="AAPL", side="SELL",
                   price=120.0, qty=10, days_ago=1)
    # 刻意不造日历 → 全靠工作日启发式
    from datetime import date as _d
    r = perf.daily_returns(STOCK_SID, days=7)
    assert r["calendar_confidence"] == "suspected"
    assert r["dates"], "日历空的时候序列整个没了"
    # 🔴 整段都是猜的时候**不许报 0**。这个字段本轮就是因为「23 天全靠猜、报 0」
    # 被抓出来的：它算的是 `len(known)`，与 `off` 的处理耦合，动一下就会无声退回 0。
    assert r["heuristic_days"] == len(r["dates"]) - len(r.get("off_calendar_days") or [])
    assert all(_d.fromisoformat(d).weekday() < 5 for d in r["dates"]
               if d not in (r.get("off_calendar_days") or []))


def test_stock_series_prefers_live_over_paper(db):
    """真金白银优先 —— paper 是模拟撮合，掺进来会污染判定。"""
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="paper", market="a_share")
    _mk_calendar(db, "a_share", [_date(2026, 7, 20) + timedelta(days=i) for i in range(5)])
    for mode, price in (("live", 110.0), ("paper", 200.0)):
        _mk_stock_fill(db, mode=mode, side="BUY", price=100.0, qty=100,
                       on=_date(2026, 7, 20))
        _mk_stock_fill(db, mode=mode, side="SELL", price=price, qty=100,
                       on=_date(2026, 7, 22))

    r = perf.daily_returns(STOCK_SID,
                           since=datetime(2026, 7, 20), until=datetime(2026, 7, 24, 23))
    assert r["basis"] == "live_fills"
    assert sum(r["returns"]) * r["capital"] == pytest.approx(1000.0, abs=1e-4)


def test_stock_series_none_when_no_fills(db):
    _mk_strategy(db, STOCK_SID, mode="paper", market="us_stock")
    r = perf.daily_returns(STOCK_SID, days=30)
    assert r["basis"] == "none" and "没有可算收益" in r["reason"]


def test_stock_series_backfills_the_uncovered_tail(db):
    """🔴 日历盖不到窗口尾部时**补齐并降级**，⛔ 不许让序列尾部悄悄少几天。

    `trading_days()` 是给缺口扫描用的，超出覆盖范围时它只回「日历盖住的那段」——
    对缺口扫描是对的，对收益序列是灾难：少掉的恰恰是最近、最该看的那几天。
    生产上这是**常态**（日历靠收盘后的指数 bar 自举，天然滞后一天以上）。
    """
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    # 日历只造到 7/22，但窗口要到 7/24
    _mk_calendar(db, "a_share", [_date(2026, 7, 20) + timedelta(days=i) for i in range(3)])
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, on=_date(2026, 7, 20))
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, on=_date(2026, 7, 22))

    r = perf.daily_returns(STOCK_SID,
                           since=datetime(2026, 7, 20), until=datetime(2026, 7, 24, 23))
    assert "2026-07-23" in r["dates"] and "2026-07-24" in r["dates"], \
        f"日历盖不到的尾部被丢了：{r['dates']}"
    # ⭐ 「日历滞后于收盘」是常态 → partial，不是 suspected。
    # 两态的话这个标记会恒亮，等于没有标记。
    assert r["calendar_confidence"] == "partial"
    assert r["heuristic_days"] == 2
    assert r["window_days"] == 5


def test_stock_series_certain_only_when_calendar_covers_the_window(db):
    """三态的另一半：日历盖满窗口才配叫 certain。"""
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_calendar(db, "a_share", [_date(2026, 7, 20) + timedelta(days=i) for i in range(5)])
    _mk_stock_fill(db, side="BUY", price=100.0, qty=100, on=_date(2026, 7, 20))
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, on=_date(2026, 7, 22))

    r = perf.daily_returns(STOCK_SID,
                           since=datetime(2026, 7, 20), until=datetime(2026, 7, 24, 23))
    assert r["calendar_confidence"] == "certain"
    assert r["heuristic_days"] == 0


def test_stock_series_reports_unbucketed_and_uncosted(db):
    """⚠️ 诚实度字段在 `strategy_pnl` 报了、序列这边也必须报 —— 同一个事实
    在两个出口说法不一致，比不说更糟。"""
    from datetime import date as _date
    _mk_strategy(db, STOCK_SID, mode="live", market="a_share")
    _mk_calendar(db, "a_share", [_date(2026, 7, 20) + timedelta(days=i) for i in range(5)])
    # 卖多于已知买入 → 成本未知；外加一行 mode 不认识的
    _mk_stock_fill(db, side="SELL", price=120.0, qty=100, on=_date(2026, 7, 22))
    _mk_stock_fill(db, mode="shadow", side="BUY", price=100.0, qty=100,
                   on=_date(2026, 7, 21))

    r = perf.daily_returns(STOCK_SID,
                           since=datetime(2026, 7, 20), until=datetime(2026, 7, 24, 23))
    assert r["has_uncosted_sell"] is True
    assert r["unbucketed_fills"] == {"shadow": 1}
