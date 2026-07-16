"""recommend_stocks 回归测试 —— 锁死架构下沉（recommend_engine）后的核心分支行为。

覆盖四条核心流水线分支（与一次性 dump+diff 验证同源，长期回归网）：
- 候选合并优先级：信号候选优先于选股器候选（同 symbol 保留信号的 strength/reasons）
- 买得起闸门剔除：一手成本超单股上限(max_position_pct) / 超可用现金(cash) 透明剔除
- 三重闸门 BUY 判定：评级 BUY + affordable + risk_passed 才进 buys；BUY 但买不起进 skipped
- 持仓 SELL/HOLD 分流：持仓走处置建议，无评分数据走 N/A

业务逻辑全在 recommend_engine，工具层只包 ToolEnvelope，故 mock 打在引擎子模块上。
"""
import os
import sys
from contextlib import ExitStack
from datetime import date, datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

from agents.tools.recommend_tools import recommend_stocks  # noqa: E402

_FIXED_DT = datetime(2026, 7, 6, 16, 0, 0)
_SIGNAL_DATE = date(2026, 7, 3)


def _fake_signal_cands(min_strength, held):
    cands = [
        {"symbol": "600001.SH", "strength": 0.9, "entry_price": 30.0, "stop_loss": 27.0,
         "reasons": ["金叉"], "source": "signal"},
        {"symbol": "600002.SH", "strength": 0.8, "entry_price": 40.0, "stop_loss": 36.0,
         "reasons": ["MACD 上穿"], "source": "signal"},
        {"symbol": "300001.SZ", "strength": 0.7, "entry_price": 15.0, "stop_loss": 13.0,
         "reasons": ["RSI 回升"], "source": "signal"},   # 非主板 -> 过滤
        {"symbol": "600003.SH", "strength": 0.5, "entry_price": 8.0, "stop_loss": 7.0,
         "reasons": ["布林下轨"], "source": "signal"},   # ST -> 过滤
    ]
    return cands, _SIGNAL_DATE, 12


def _fake_screener_cands(pool_id, held, limit):
    return [
        # 与 600002 signal 重复 -> 信号优先，仅补 roe
        {"symbol": "600002.SH", "name": "股票B", "price": 40.0, "roe": 15, "strength": None,
         "reasons": ["基本面：ROE 15%"], "source": "screener"},
        {"symbol": "600004.SH", "name": "股票D", "price": None, "roe": 12, "strength": None,
         "reasons": ["基本面：ROE 12%"], "source": "screener"},   # 后续 HOLD
        {"symbol": "600005.SH", "name": "股票E", "price": None, "roe": 10, "strength": None,
         "reasons": ["基本面：ROE 10%"], "source": "screener"},   # 太贵 -> maxpos 剔除
        {"symbol": "600006.SH", "name": "股票F", "price": None, "roe": 9, "strength": None,
         "reasons": ["基本面：ROE 9%"], "source": "screener"},    # 太贵 -> cash 剔除
    ]


_NAMES = {
    "600001.SH": "股票A", "600002.SH": "股票B", "300001.SZ": "创业板X",
    "600003.SH": "ST困境", "600004.SH": "股票D", "600005.SH": "股票E", "600006.SH": "股票F",
}
_PRICES = {"600001.SH": 30.0, "600002.SH": 40.0, "600004.SH": 50.0,
           "600005.SH": 120.0, "600006.SH": 90.0}

_POSITIONS = [
    {"symbol": "600010.SH", "name": "持仓A", "quantity": 100, "market_value": 5000.0,
     "unrealized_pnl_pct": 0.12},   # SELL
    {"symbol": "600011.SH", "name": "持仓B", "quantity": 200, "market_value": 8000.0,
     "unrealized_pnl_pct": -0.05},  # HOLD
    {"symbol": "600012.SH", "name": "持仓C", "quantity": 100, "market_value": 3000.0,
     "unrealized_pnl_pct": 0.0},    # 无 agg -> N/A
    {"symbol": "600013.SH", "name": "持仓D", "quantity": 0, "market_value": 0.0,
     "unrealized_pnl_pct": 0.0},    # qty0 -> 不入 held，无 agg -> N/A
]

_AGG = {
    "600001.SH": {"recommendation": "BUY", "name": "股票A", "composite": 82,
                  "price": {"latest": 30.0}, "stop_loss": {"price": 27.0},
                  "suggested": {"shares": 100, "lots": 1, "amount": 3000.0,
                                "affordable": True, "risk_passed": True, "capped_by": None}},
    "600002.SH": {"recommendation": "BUY", "name": "股票B", "composite": 75,
                  "price": {"latest": 40.0}, "stop_loss": {"price": 36.0},
                  "suggested": {"shares": 100, "lots": 1, "amount": 4000.0,
                                "affordable": False, "risk_passed": True, "capped_by": "cash"}},
    "600004.SH": {"recommendation": "HOLD", "name": "股票D", "composite": 60,
                  "price": {"latest": 50.0}, "stop_loss": {"price": 45.0},
                  "suggested": {"shares": 0, "affordable": True, "risk_passed": True}},
    "600010.SH": {"recommendation": "SELL", "name": "持仓A", "composite": 35,
                  "current_position": {"shares": 100, "value": 5000.0, "pct": 0.1},
                  "stop_loss": {"price": 45.0}},
    "600011.SH": {"recommendation": "HOLD", "name": "持仓B", "composite": 58,
                  "current_position": {"shares": 200, "value": 8000.0, "pct": 0.16},
                  "stop_loss": {"price": 38.0}},
    # 600012 缺席 -> N/A 分支
}


class _FakeCalc:
    def get_current_positions(self):
        return [dict(p) for p in _POSITIONS]


def _run():
    """跑一次全 mock 的 recommend_stocks（收盘后场景，跳过盘中分钟线），返回 .data。"""
    with ExitStack() as es:
        e = es.enter_context
        e(patch("recommend_engine.candidates._fetch_signal_candidates", _fake_signal_cands))
        e(patch("recommend_engine.candidates._fetch_screener_candidates", _fake_screener_cands))
        e(patch("recommend_engine.candidates._stock_names",
                lambda syms: {s: _NAMES[s] for s in syms if s in _NAMES}))
        e(patch("recommend_engine.candidates._latest_close_map",
                lambda syms: {s: _PRICES[s] for s in syms if s in _PRICES}))
        e(patch("recommend_engine.candidates._is_main_board",
                lambda sym: not sym.startswith("300")))
        e(patch("recommend_engine.scoring._aggregate_many",
                lambda syms: {s: _AGG[s] for s in syms if s in _AGG}))
        e(patch("recommend_engine.engine._session_phase", lambda *a, **k: "after_close"))
        e(patch("recommend_engine.engine._now_sh", lambda: _FIXED_DT))
        e(patch("recommend_engine.engine.get_total_capital", lambda: 50000.0))
        e(patch("recommend_engine.engine.get_max_position_pct", lambda: 0.2))
        e(patch("recommend_engine.engine.build_broker_info", lambda tc: {"cash": 8000.0}))
        e(patch("recommend_engine.engine.PortfolioCalculator", _FakeCalc))
        e(patch("decision_log.record_decision", lambda *a, **k: None))
        return recommend_stocks(pool_id=None, limit=5, min_strength=0.3).data


def test_market_state_active_and_shape():
    d = _run()
    assert d["market_state"] == "active"
    assert d["session_phase"] == "after_close"
    assert d["provisional"] is False
    assert d["total_capital"] == 50000.0
    assert d["max_single_amount"] == 10000.0
    # 主板+ST 过滤后（600001/600002/600004）
    assert d["candidates_after_filter"] == 3


def test_triple_gate_buy():
    """三重闸门：只有 600001（BUY+affordable+risk_passed）进 buys。"""
    d = _run()
    assert [b["symbol"] for b in d["buys"]] == ["600001.SH"]
    b = d["buys"][0]
    assert b["recommendation"] == "BUY"
    assert b["suggested"]["affordable"] is True
    assert b["suggested"]["risk_passed"] is True
    assert b["composite"] == 82


def test_candidate_merge_priority():
    """600002 同时来自信号与选股器，信号优先：strength/reasons 取信号侧。"""
    d = _run()
    buy = d["buys"][0]
    # 信号候选的 strength 与 reasons 被保留（600001 信号）
    assert buy["strength"] == 0.9
    assert buy["reasons"] == ["金叉"]


def test_affordability_gate_skips():
    """买得起闸门：600005 超单股上限(maxpos)、600006 超现金(cash) 预筛剔除；
    600002 评级 BUY 但 affordable=False 三重闸门剔除(reason=cash)。"""
    d = _run()
    skipped = {s["symbol"]: s["reason"] for s in d["skipped_unaffordable"]}
    assert skipped["600005.SH"] == "max_position_pct"
    assert skipped["600006.SH"] == "cash"
    assert skipped["600002.SH"] == "cash"
    assert len(d["skipped_unaffordable"]) == 3


def test_holdings_sell_hold_split():
    """持仓分流：SELL/HOLD 带评分，无评分数据走 N/A。"""
    d = _run()
    advice = {h["symbol"]: h for h in d["holdings_advice"]}
    assert advice["600010.SH"]["recommendation"] == "SELL"
    assert advice["600011.SH"]["recommendation"] == "HOLD"
    assert advice["600012.SH"]["recommendation"] == "N/A"
    assert advice["600012.SH"]["note"] == "评分数据不足"
    # qty0 的 600013 不入 held 但仍在持仓建议里走 N/A
    assert advice["600013.SH"]["recommendation"] == "N/A"
    assert len(d["holdings_advice"]) == 4


def test_non_main_board_and_st_filtered():
    """300001.SZ(创业板)与 600003.SH(ST) 不出现在任何输出桶。"""
    d = _run()
    all_syms = (
        {b["symbol"] for b in d["buys"]}
        | {s["symbol"] for s in d["skipped_unaffordable"]}
        | {h["symbol"] for h in d["holdings_advice"]}
    )
    assert "300001.SZ" not in all_syms
    assert "600003.SH" not in all_syms
