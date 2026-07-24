"""硬风控 rule 的「今天/现在」必须按**当单市场**的时区算，不用服务器本地时钟。

RiskManager 的这套 rule 是 A 股和 crypto **共用**的（crypto 走
`execution.risk_check` → `RiskManager(crypto_risk_config).check_order`）。`check_order`
每次都把 `symbol` 塞进 kwargs，所以每条 rule 都能自己推市场。

用错时区的后果（都在硬风控上）：
- `MaxDailyLossRule` 的当日亏损计数在**错误的时刻**清零 —— crypto 本该 UTC 午夜，
  按本地会在北京午夜（= UTC 16:00）重置，凌晨那段的累计亏损被提前抹掉。
- `ConsecutiveLossRule` 的「距上次亏损几天」偏 1 天 → 暂停期算错。

这些测试冻结「今天」验证 rule 确实用了 market 化的日期，不是 `datetime.now()`。
"""
from datetime import date

import trading_engine.risk.rules as rules_mod
from trading_engine.risk.rules import (
    ConsecutiveLossRule,
    MaxDailyLossRule,
    TradingHoursRule,
    _market_of,
)


class TestMarketInference:
    def test_infers_from_symbol(self):
        assert _market_of({"symbol": "BTCUSDT.BN"}) == "crypto"
        assert _market_of({"symbol": "600519.SH"}) == "a_share"
        assert _market_of({"symbol": "00700.HK"}) == "hk_stock"

    def test_missing_symbol_defaults_a_share(self):
        """symbol 缺失 → A 股（服务器在上海，与旧 datetime.now() 行为一致）。"""
        assert _market_of({}) == "a_share"
        assert _market_of({"symbol": None}) == "a_share"


class TestMaxDailyLossResetsOnMarketDay:
    def test_reset_boundary_follows_the_order_market(self, monkeypatch):
        """当日亏损计数的「今天」按当单市场算。

        冻结 market_today：crypto → 07-21，a_share → 07-22。同一条 rule 实例先收
        crypto 单再收 A 股单，两次的 today 不同 → 第二次必然触发跨天重置分支。
        """
        days = {"crypto": date(2026, 7, 21), "a_share": date(2026, 7, 22)}
        monkeypatch.setattr(rules_mod, "market_today", lambda m: days[m])

        rule = MaxDailyLossRule(max_daily_loss=1000.0)
        assert rule.last_reset_date is None, "构造时不碰时间"

        rule.check(current_pnl=-100.0, symbol="BTCUSDT.BN")
        assert rule.last_reset_date == date(2026, 7, 21), "crypto 单 → UTC 那天"

        rule.check(current_pnl=-50.0, symbol="600519.SH")
        assert rule.last_reset_date == date(2026, 7, 22), "A 股单 → 上海那天，触发重置"

    def test_threshold_logic_unchanged(self, monkeypatch):
        """只改了时区口径，判定阈值一字未动：亏损达线仍然拒单。"""
        monkeypatch.setattr(rules_mod, "market_today", lambda m: date(2026, 7, 22))
        rule = MaxDailyLossRule(max_daily_loss=1000.0)
        assert rule.check(current_pnl=-1500.0, symbol="600519.SH").passed is False
        assert rule.check(current_pnl=-500.0, symbol="600519.SH").passed is True


class TestConsecutiveLossPauseUsesMarketDay:
    def test_days_since_uses_crypto_utc_today(self, monkeypatch):
        """crypto 的暂停期按 UTC 今天算。

        冻结 crypto today=07-22，上次亏损 07-21 → 距今 1 天。pause_days=3 → 仍在暂停期，
        必须拒。若错用了别的市场的 today（偏一天）这个断言就会漂。
        """
        monkeypatch.setattr(rules_mod, "market_today",
                            lambda m: date(2026, 7, 22) if m == "crypto" else date(2026, 7, 25))
        rule = ConsecutiveLossRule(max_consecutive=3, pause_days=3)
        r = rule.check(recent_closed_pnls=[-1, -1, -1], last_loss_date="2026-07-21",
                       symbol="BTCUSDT.BN")
        assert r.passed is False and "距上次亏损仅 1 天" in r.message

    def test_pause_expired_lets_through(self, monkeypatch):
        monkeypatch.setattr(rules_mod, "market_today", lambda m: date(2026, 7, 30))
        rule = ConsecutiveLossRule(max_consecutive=3, pause_days=3)
        # 连亏 3 笔但暂停期已过 → 仍给 WARNING（复盘建议），但不因「暂停期内」那条拒
        r = rule.check(recent_closed_pnls=[-1, -1, -1], last_loss_date="2026-07-21",
                       symbol="BTCUSDT.BN")
        assert "距上次亏损" not in r.message


class TestTradingHoursUsesMarketNow:
    def test_now_follows_order_market(self, monkeypatch):
        """交易时段判定的 now 按当单市场时区（这条 rule 当前不激活，但口径要对）。"""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # 冻结成一个 aware 时刻，验证 rule 取的是 .time() 且来自 market_now
        frozen = datetime(2026, 7, 22, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        monkeypatch.setattr(rules_mod, "market_now", lambda m: frozen)
        rule = TradingHoursRule([("09:30", "11:30"), ("13:00", "15:00")])
        assert rule.check(symbol="600519.SH").passed is True

    def test_outside_hours_blocks(self, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        frozen = datetime(2026, 7, 22, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        monkeypatch.setattr(rules_mod, "market_now", lambda m: frozen)
        rule = TradingHoursRule([("09:30", "11:30"), ("13:00", "15:00")])
        assert rule.check(symbol="600519.SH").passed is False
