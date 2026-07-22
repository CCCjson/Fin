"""crypto「当日」口径回归 —— 那个每天 8 小时的黑洞。

## 修的是什么

`crypto_fills.trade_time` 存 naive UTC（币安 ms 时间戳），但 `fees_on` / `realized_on`
的调用方曾经传服务器本地的 `date.today()`，SQL 又写成 `DATE(trade_time) = :day`。
实测：本地 07-22 凌晨 3:00 的一买一卖，`fees_on(date.today())` 返回 **0.0**（应为 1.1）。

后果不是「少显示一个数字」：

- `check_daily_loss` 的当日回撤熔断吃的是 `realized_on` —— 凌晨爆亏不计入，白天照常排单
- `max_fees_per_day_usdt` 的费用护栏吃的是 `fees_on` —— 同样瞎掉那 8 小时

而且那些成交会被算进**昨天**，把昨天的数字虚高。crypto 是 7×24 市场，本地 00:00–08:00
正是行情最容易出事的时段。

## 现在的口径

crypto 的市场时区 = UTC（币安日线 openTime/closeTime 是 UTC 00:00 边界、资金费率
UTC 00/08/16 结算）。「当日」一律 `market_today(CRYPTO)` + `market_day_bounds(CRYPTO)`。
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from common.market import CRYPTO
from common.market_time import market_day_bounds, market_today

pytestmark = pytest.mark.baseline


@pytest.fixture
def mem_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data_engine.storage.models  # noqa: F401 — 注册所有表
    from data_engine.storage import database as db
    from data_engine.storage.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    make = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(db, "get_session", lambda: make())
    return make


def _add_fill(mem_db, *, trade_id, price, qty, is_buyer, fee, trade_time, fee_asset="USDT"):
    from data_engine.storage.models import CryptoFill
    s = mem_db()
    try:
        s.add(CryptoFill(source="spot", symbol="BTCUSDT.BN", trade_id=str(trade_id),
                         price=price, quantity=qty, quote_qty=price * qty,
                         commission=fee, commission_asset=fee_asset,
                         is_buyer=1 if is_buyer else 0, trade_time=trade_time))
        s.commit()
    finally:
        s.close()


def _utc_of_local(y, m, d, hh, offset_hours=8):
    """构造「某个 UTC+offset 本地时刻」对应的 naive UTC，用来模拟 Jason 的凌晨。"""
    tz = timezone(timedelta(hours=offset_hours))
    return datetime(y, m, d, hh, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


class TestEarlyMorningIsNotLost:
    """本地凌晨（= 前一个 UTC 日的傍晚）的成交，必须计入它**真正所属**的那个市场日。"""

    def test_fees_land_on_the_utc_day(self, mem_db):
        from crypto_intel_engine import cost_basis as cb

        # UTC+8 的 07-22 凌晨 3:00 → naive UTC 07-21 19:00
        t = _utc_of_local(2026, 7, 22, 3)
        assert t == datetime(2026, 7, 21, 19, 0)
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.5, trade_time=t - timedelta(minutes=10))
        _add_fill(mem_db, trade_id=2, price=120.0, qty=1.0, is_buyer=False,
                  fee=0.6, trade_time=t)

        # 它属于 crypto 市场日 07-21（UTC），两笔手续费都在
        assert cb.fees_on(date(2026, 7, 21)) == pytest.approx(1.1)
        # ⛔ 绝不能落到 07-22（那是旧实现按本地日切出来的答案）
        assert cb.fees_on(date(2026, 7, 22)) == pytest.approx(0.0)

    def test_realized_pnl_lands_on_the_utc_day(self, mem_db):
        from crypto_intel_engine import cost_basis as cb

        t = _utc_of_local(2026, 7, 22, 3)
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.0, trade_time=t - timedelta(minutes=10))
        _add_fill(mem_db, trade_id=2, price=120.0, qty=1.0, is_buyer=False,
                  fee=0.0, trade_time=t)

        assert cb.realized_on(date(2026, 7, 21)) == pytest.approx(20.0)
        assert cb.realized_on(date(2026, 7, 22)) == pytest.approx(0.0)

    def test_replay_groups_by_market_day_not_naive_date(self, mem_db):
        """`closed[].date` 是市场日 —— 它要和 `realized_on(market_today(CRYPTO))` 对得上。"""
        from crypto_intel_engine import cost_basis as cb

        t = _utc_of_local(2026, 7, 22, 3)
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.0, trade_time=t - timedelta(minutes=10))
        _add_fill(mem_db, trade_id=2, price=120.0, qty=1.0, is_buyer=False,
                  fee=0.0, trade_time=t)
        closed = cb.replay("BTCUSDT.BN")["BTCUSDT.BN"]["closed"]
        assert [c["date"] for c in closed] == ["2026-07-21"]


class TestGuardrailsSeeTodaysFills:
    """护栏读的「今天」必须能看到此刻刚发生的成交 —— 不管现在本地几点。"""

    def test_fee_guardrail_sees_a_fill_from_a_moment_ago(self, mem_db):
        from common.market_time import utc_now
        from crypto_strategy.engine import CryptoStrategyEngine

        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.42, trade_time=utc_now() - timedelta(minutes=1))
        # 旧实现在本地 00:00–08:00 这个窗口跑会返回 0
        assert CryptoStrategyEngine()._today_fees() == pytest.approx(0.42)

    def test_circuit_breaker_sees_a_close_from_a_moment_ago(self, mem_db):
        from common.market_time import utc_now
        from crypto_strategy.engine import CryptoStrategyEngine

        now = utc_now()
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.0, trade_time=now - timedelta(minutes=5))
        _add_fill(mem_db, trade_id=2, price=80.0, qty=1.0, is_buyer=False,
                  fee=0.0, trade_time=now - timedelta(minutes=1))
        # 亏 20，必须进当日熔断的原料
        assert CryptoStrategyEngine()._realized_today() == pytest.approx(-20.0)


class TestEngineWiringWithFrozenClock:
    """⭐ 确定性地钉住引擎那一端 —— 上面那组按真实时钟跑，白天跑对旧实现也是绿的。

    这里把引擎看到的「今天」冻在 2026-07-21，再放一笔「本地 07-22 凌晨 3:00」的成交
    （UTC 07-21 19:00）。它属于 crypto 市场日 07-21，引擎必须看得见 —— 旧实现在这个
    场景下会去问 07-22，返回 0。
    """

    LOCAL_EARLY_MORNING = datetime(2026, 7, 21, 19, 0)     # = UTC+8 的 07-22 03:00
    MARKET_DAY = date(2026, 7, 21)

    @pytest.fixture(autouse=True)
    def _freeze(self, monkeypatch):
        monkeypatch.setattr("crypto_strategy.engine.market_today",
                            lambda market: self.MARKET_DAY)

    def test_engine_fees_see_the_early_morning_fill(self, mem_db):
        from crypto_strategy.engine import CryptoStrategyEngine
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.37, trade_time=self.LOCAL_EARLY_MORNING)
        assert CryptoStrategyEngine()._today_fees() == pytest.approx(0.37)

    def test_engine_realized_sees_the_early_morning_close(self, mem_db):
        from crypto_strategy.engine import CryptoStrategyEngine
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.0, trade_time=self.LOCAL_EARLY_MORNING - timedelta(minutes=10))
        _add_fill(mem_db, trade_id=2, price=70.0, qty=1.0, is_buyer=False,
                  fee=0.0, trade_time=self.LOCAL_EARLY_MORNING)
        assert CryptoStrategyEngine()._realized_today() == pytest.approx(-30.0)

    def test_next_utc_day_fill_is_not_counted(self, mem_db):
        """半开区间的另一端：UTC 07-22 00:00 那笔不算进 07-21。"""
        from crypto_strategy.engine import CryptoStrategyEngine
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=9.99, trade_time=datetime(2026, 7, 22, 0, 0))
        assert CryptoStrategyEngine()._today_fees() == pytest.approx(0.0)

    def test_last_second_of_the_utc_day_is_counted(self, mem_db):
        from crypto_strategy.engine import CryptoStrategyEngine
        _add_fill(mem_db, trade_id=1, price=100.0, qty=1.0, is_buyer=True,
                  fee=0.11, trade_time=datetime(2026, 7, 21, 23, 59, 59))
        assert CryptoStrategyEngine()._today_fees() == pytest.approx(0.11)


class TestDayBoundsContract:
    """crypto 的市场日边界就是 UTC 自然日 —— 钉住这条，防有人把它改回本地。"""

    def test_crypto_day_is_utc_midnight_to_midnight(self):
        start, end = market_day_bounds(CRYPTO, date(2026, 7, 22))
        assert start == datetime(2026, 7, 22, 0, 0)
        assert end == datetime(2026, 7, 23, 0, 0)

    def test_market_today_matches_utc_date(self):
        assert market_today(CRYPTO) == datetime.now(tz=timezone.utc).date()
