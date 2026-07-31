"""四市场会话判定（S6）。

两条最要紧的：

1. **crypto 恒开市** —— 把 A 股的「收盘后不用刷新」套到它头上，crypto 的预警/止损
   会在每天绝大部分时间里瞎掉。
2. **与 `recommend_engine/session.py` 对 A 股同口径** —— 两个模块对同一时刻给出不同
   答案，比不准更糟（recommend 链路依赖那一个，取价链路依赖这一个）。
"""
from datetime import datetime

import pytest

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK
from common.market_session import (
    AFTER_CLOSE,
    CLOSED_DAY,
    INTRADAY,
    PRE_MARKET,
    describe,
    is_open,
    session_phase,
)

# 2026-07-31 是周五，2026-08-01 周六
FRI = "2026-07-31"
SAT = "2026-08-01"


def _at(day: str, hh: int, mm: int = 0) -> datetime:
    return datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00")


# ── crypto：7×24 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("day,hh", [(FRI, 3), (FRI, 12), (SAT, 3), (SAT, 23)])
def test_crypto_is_always_intraday(day, hh):
    """⭐ 周末凌晨三点也是交易中 —— 它没有休市这个概念。"""
    assert session_phase(CRYPTO, _at(day, hh)) == INTRADAY
    assert is_open(CRYPTO, _at(day, hh)) is True


# ── 股票：四态 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("market", [A_SHARE, HK_STOCK, US_STOCK])
def test_weekend_is_closed_day(market):
    assert session_phase(market, _at(SAT, 10)) == CLOSED_DAY
    assert is_open(market, _at(SAT, 10)) is False


@pytest.mark.parametrize("market,hh,want", [
    (A_SHARE, 9, PRE_MARKET),
    (A_SHARE, 10, INTRADAY),
    (A_SHARE, 12, INTRADAY),        # ⚠️ 午休算盘中，与 recommend 那边一致
    (A_SHARE, 16, AFTER_CLOSE),
    (HK_STOCK, 9, PRE_MARKET),
    (HK_STOCK, 15, INTRADAY),       # 港股收得比 A 股晚一小时
    (HK_STOCK, 17, AFTER_CLOSE),
    (US_STOCK, 9, PRE_MARKET),
    (US_STOCK, 10, INTRADAY),
    (US_STOCK, 17, AFTER_CLOSE),
])
def test_phases(market, hh, want):
    assert session_phase(market, _at(FRI, hh)) == want


def test_boundaries_are_half_open():
    """开盘那一刻算盘中，收盘那一刻算已收盘。"""
    assert session_phase(A_SHARE, _at(FRI, 9, 30)) == INTRADAY
    assert session_phase(A_SHARE, _at(FRI, 9, 29)) == PRE_MARKET
    assert session_phase(A_SHARE, _at(FRI, 15, 0)) == AFTER_CLOSE
    assert session_phase(A_SHARE, _at(FRI, 14, 59)) == INTRADAY


# ── 与 recommend_engine 同口径 ───────────────────────────────────────────

@pytest.mark.parametrize("day,hh,mm", [
    (FRI, 8, 0), (FRI, 9, 29), (FRI, 9, 30), (FRI, 11, 45),
    (FRI, 12, 30), (FRI, 14, 59), (FRI, 15, 0), (FRI, 20, 0),
    (SAT, 10, 0),
])
def test_a_share_matches_recommend_engine(day, hh, mm):
    """🔴 两个模块对 A 股必须给出**一模一样**的答案。

    recommend 链路依赖 `recommend_engine/session.py`，取价链路依赖本模块 ——
    对同一时刻分歧的话，「盘中」这个词在系统里就有两种意思了。
    ⚠️ 本卡刻意**不合并**那两个模块（那个是 A 股专用且已被 recommend 深度依赖），
    所以只能靠这条门禁钉住。
    """
    from recommend_engine.session import _session_phase
    t = _at(day, hh, mm)
    assert session_phase(A_SHARE, t) == _session_phase(t)


# ── 人话 ────────────────────────────────────────────────────────────────

def test_describe_is_human_readable():
    assert describe(CRYPTO, _at(SAT, 3)) == "交易中"
    assert describe(A_SHARE, _at(SAT, 10)) == "休市"
    assert describe(A_SHARE, _at(FRI, 16)) == "已收盘"


def test_unknown_market_falls_back_like_the_rest_of_the_project():
    """未知市场随 `normalize_market` 落到 A 股 —— 与全项目 fallback 口径一致。"""
    assert session_phase("没这个市场", _at(SAT, 10)) == CLOSED_DAY
