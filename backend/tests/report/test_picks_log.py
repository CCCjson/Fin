"""13.2-5 行为基线：买入推荐留痕 → 上期回顾回读 的往返。

废掉全量报告后，Ch6「上期回顾」不再读 AnalysisReport.data_snapshot（那是个只有
报告自己读写的自引用闭环），改读 DecisionLog——recommend_engine 的注释原话就是
「每条 BUY 推荐入 DecisionLog，供『上次推荐对不对』归因」。

后面 220 行的回算逻辑（止盈止损先后触发、胜率、平均涨幅）一行没动，所以本测试
只钉「取数 + 字段映射」这一段：回读出来的 rec dict 必须逐字还原成回算认得的形状。

用内存 SQLite，不碰生产库。
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data_engine.storage.models import Base, DecisionLog
from report_engine import picks_log

pytestmark = pytest.mark.baseline

TODAY = date(2026, 7, 10)


def _sh(y, m, d, hh=0, mm=0) -> datetime:
    """按**北京墙钟**写用例，返回列里真正存的 naive UTC 值。

    `DecisionLog.created_at` 是 `server_default=func.now()` 写的 → SQLite 落 **UTC**，
    而「上期推荐是哪一批」是按 Jason 的北京日历分的。用例里直接写北京时间可读，
    换算收在这一个函数里。（原来用例直接把北京墙钟当列值写，等于假设列存本地时间 ——
    那正是 `picks_log` 里被修掉的那个 8 小时错位。）
    """
    return (datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=8)))
            .astimezone(timezone.utc).replace(tzinfo=None))


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _row(symbol, created_at, *, source=picks_log.PICKS_SOURCE, action="BUY",
         price=10.0, confidence=80.0, reasons='["MACD", "RSI"]', name=None):
    return DecisionLog(
        decision_id=f"d_{symbol}_{created_at.isoformat()}", source=source,
        symbol=symbol, name=name or f"名_{symbol}", action=action, recommendation=action,
        confidence=confidence, entry_price=price, stop_loss=price * 0.95,
        take_profit=price * 1.1, reasons=reasons, created_at=created_at,
    )


# ── 回读 ──────────────────────────────────────────────────────────────────

def test_no_history_returns_empty(session):
    recs, batch_date = picks_log.fetch_last_picks(session, TODAY)
    assert recs == []
    assert batch_date is None


def test_fetches_only_the_latest_batch(session):
    old = _sh(2026, 7, 1, 9, 0)
    latest = _sh(2026, 7, 8, 15, 30)
    session.add_all([
        _row("600000.SH", old),
        _row("600519.SH", latest),
        _row("000001.SZ", _sh(2026, 7, 8, 15, 31)),        # 同一天，同批
    ])
    session.commit()

    recs, batch_date = picks_log.fetch_last_picks(session, TODAY)
    assert batch_date == date(2026, 7, 8)
    assert {r["symbol"] for r in recs} == {"600519.SH", "000001.SZ"}


def test_ignores_other_sources_and_actions(session):
    ts = _sh(2026, 7, 8, 10, 0)
    session.add_all([
        _row("600519.SH", ts),
        _row("000001.SZ", ts, source="moneybill_recommend"),   # 别的引擎写的
        _row("300750.SZ", ts, action="SELL"),                  # 卖出建议
    ])
    session.commit()

    recs, _ = picks_log.fetch_last_picks(session, TODAY)
    assert [r["symbol"] for r in recs] == ["600519.SH"]


def test_excludes_picks_made_on_or_after_current_period_end(session):
    """「上期」必须严格早于本期截止日，否则会拿今天刚推的票回顾今天。"""
    session.add_all([
        _row("600519.SH", _sh(2026, 7, 10, 0, 0)),     # 北京 07-10 零点整 = 本期，排除
        _row("600000.SH", _sh(2026, 7, 9, 23, 59)),    # 北京 07-09 深夜 = 上期，要取到
    ])
    session.commit()

    recs, batch_date = picks_log.fetch_last_picks(session, TODAY)
    assert [r["symbol"] for r in recs] == ["600000.SH"]
    assert batch_date == date(2026, 7, 9)


def test_rec_dict_shape_matches_what_the_scoring_loop_expects(session):
    """回算逻辑读 symbol/name/price/stop_loss/take_profit/strategy/composite_score。"""
    session.add(_row("600519.SH", _sh(2026, 7, 8, 10, 0), price=1700.0,
                     confidence=88.5, name="贵州茅台"))
    session.commit()

    recs, _ = picks_log.fetch_last_picks(session, TODAY)
    assert recs == [{
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "price": 1700.0,
        "stop_loss": pytest.approx(1615.0),
        "take_profit": pytest.approx(1870.0),
        "strategy": "MACD, RSI",
        "composite_score": 88.5,
    }]


@pytest.mark.parametrize("reasons,expected", [
    ('["MACD", "RSI"]', "MACD, RSI"),
    ('"单条理由"', "单条理由"),
    ("裸字符串不是 JSON", "裸字符串不是 JSON"),
    (None, ""),
    ("", ""),
])
def test_strategy_text_handles_every_reasons_shape(reasons, expected):
    assert picks_log._strategy_text(reasons) == expected


def test_missing_confidence_becomes_empty_string_not_none(session):
    """旧 rec dict 里 composite_score 缺失时是 ""，prompt 直接拼进模板。"""
    session.add(_row("600519.SH", _sh(2026, 7, 8, 10, 0), confidence=None))
    session.commit()
    recs, _ = picks_log.fetch_last_picks(session, TODAY)
    assert recs[0]["composite_score"] == ""


def test_batch_ordered_by_score_desc(session):
    ts = _sh(2026, 7, 8, 10, 0)
    session.add_all([_row("600000.SH", ts, confidence=70.0),
                     _row("600519.SH", ts + timedelta(seconds=1), confidence=90.0)])
    session.commit()
    recs, _ = picks_log.fetch_last_picks(session, TODAY)
    assert [r["symbol"] for r in recs] == ["600519.SH", "600000.SH"]


# ── 写入 → 回读 往返 ──────────────────────────────────────────────────────

def test_record_then_fetch_round_trip(session, monkeypatch):
    """report_picks 写下的推荐，下次 report_strategy 必须原样读回来。"""
    monkeypatch.setattr("decision_log.get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)

    recommendations = [{
        "symbol": "600519.SH", "name": "贵州茅台", "price": 1700.0,
        "stop_loss": 1615.0, "take_profit": 1870.0,
        "composite_score": 88.5, "resonance_strategies": ["MACD", "RSI"],
        "risk_tier": {"label": "稳健"},
    }]
    assert picks_log.record_picks(recommendations) == 1

    # 回读要求严格早于 cutoff，用明天当 period_end
    recs, batch_date = picks_log.fetch_last_picks(session, date.today() + timedelta(days=1))
    assert batch_date == date.today()
    assert recs[0]["symbol"] == "600519.SH"
    assert recs[0]["price"] == 1700.0
    assert recs[0]["stop_loss"] == 1615.0
    assert recs[0]["take_profit"] == 1870.0
    assert recs[0]["composite_score"] == 88.5
    assert recs[0]["strategy"] == "MACD, RSI"


def test_record_picks_skips_rows_without_symbol(session, monkeypatch):
    monkeypatch.setattr("decision_log.get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    assert picks_log.record_picks([{"name": "无代码"}, {"symbol": "600519.SH"}]) == 1


def test_record_picks_on_empty_list_is_a_noop(session):
    assert picks_log.record_picks([]) == 0


# ── 端到端：DecisionLog → 220 行回算 ──────────────────────────────────────

def _quote(symbol, d, o, h, low, c):
    from data_engine.storage.models import DailyQuote
    return DailyQuote(symbol=symbol, market="a_share", date=d,
                      open=o, high=h, low=low, close=c, volume=1000)


def test_previous_recommendations_end_to_end_still_scores(session):
    """换了数据源之后，那 220 行止盈/止损/胜率回算必须照常算得出来。"""
    from report_engine.data_collector import ReportDataCollector

    rec_day = _sh(2026, 7, 6, 15, 0)
    # 一只触止盈、一只触止损、一只没数据
    session.add_all([
        _row("600519.SH", rec_day, price=100.0, confidence=90.0),   # sl=95, tp=110
        _row("600000.SH", rec_day, price=100.0, confidence=80.0),
        _row("000001.SZ", rec_day, price=100.0, confidence=70.0),   # 无行情
    ])
    for d, (o, h, low, c) in zip(
            [date(2026, 7, 7), date(2026, 7, 8)],
            [(100, 112, 99, 111), (111, 113, 110, 112)], strict=True):
        session.add(_quote("600519.SH", d, o, h, low, c))           # 高点 112 ≥ 止盈 110
    session.add(_quote("600000.SH", date(2026, 7, 7), 100, 101, 90, 92))  # 低点 90 ≤ 止损 95
    session.commit()

    prev = ReportDataCollector()._collect_previous_recommendations(session, TODAY, "weekly")

    assert prev["has_previous"] is True
    assert prev["report_date"] == "2026-07-06"
    assert prev["report_title"] == "上期买入推荐（3 只）"
    assert prev["report_id"] == "picks_2026-07-06"

    by_symbol = {r["symbol"]: r for r in prev["recommendations"]}
    assert by_symbol["600519.SH"]["hit_take_profit"] is True
    assert by_symbol["600519.SH"]["status"] == "已触止盈"
    assert by_symbol["600519.SH"]["change_pct"] == pytest.approx(10.0)   # 按止盈价 110 计
    assert by_symbol["600519.SH"]["strategy"] == "MACD, RSI"
    assert by_symbol["600519.SH"]["composite_score"] == 90.0

    assert by_symbol["600000.SH"]["hit_stop_loss"] is True
    assert by_symbol["600000.SH"]["status"] == "已触止损"
    assert by_symbol["600000.SH"]["change_pct"] == pytest.approx(-5.0)

    assert by_symbol["000001.SZ"]["status"] == "暂无行情数据"

    s = prev["summary"]
    assert s == {"total": 3, "valid": 2, "winning": 1, "losing": 1,
                 "no_data": 1, "flat": 0, "win_rate": 50.0, "avg_return_pct": 2.5}


def test_previous_recommendations_without_history_says_no_previous(session):
    from report_engine.data_collector import ReportDataCollector
    prev = ReportDataCollector()._collect_previous_recommendations(session, TODAY, "weekly")
    assert prev == {"has_previous": False}
