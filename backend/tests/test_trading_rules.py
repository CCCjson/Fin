"""交易单位与结算规则（S5 批次2）。

🔴 这一层存在的理由不是「消除重复」，是防**A 股规则被套到美股头上**：
美股 T+0、无涨跌停、1 股一手，三条全都不适用。全自动交易一旦套错，
美股会莫名其妙买不进（凑不满 100 股一手）或卖不掉（以为要 T+1）——
**而且不报错，只是不成交**。
"""
import pytest

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK
from common.trading_rules import (
    can_sell_today,
    is_tradable_at,
    lot_floor,
    rules_for,
)


class TestPerMarketRules:
    def test_a_share_is_the_strict_one(self):
        r = rules_for(A_SHARE)
        assert (r.lot_size, r.t_plus_one, r.has_price_limit) == (100, True, True)

    def test_us_stock_shares_none_of_the_a_share_rules(self):
        """⛔ 三条 A 股规则**一条都不该套到美股头上**。"""
        r = rules_for(US_STOCK)
        assert r.lot_size == 1, "美股 1 股就能买"
        assert r.t_plus_one is False, "美股 T+0"
        assert r.has_price_limit is False, "美股没有个股涨跌停"

    def test_crypto_lot_is_one(self):
        """BTC 单价 6 万+，按 100 一手算 $10 万本金连一手都凑不齐。"""
        assert rules_for(CRYPTO).lot_size == 1

    def test_hk_lot_is_one_not_a_hundred(self):
        """⚠️ 港股每手股数按标的不同，引擎拿不到那张表 →
        v1 按 1（宁可粒度偏细，也不要凭空按 100 把小额单打掉）。"""
        assert rules_for(HK_STOCK).lot_size == 1

    def test_unknown_market_raises_instead_of_falling_back(self):
        """⛔ 回落到 A 股 = 给这个市场套上 100 股一手 + T+1，然后静默地买不进也卖不掉。"""
        with pytest.raises(KeyError, match="不会回落到 A 股"):
            rules_for("mars_stock")


class TestLotFloor:
    def test_a_share_rounds_to_hundred(self):
        assert lot_floor(A_SHARE, 1666.6) == 1600
        assert lot_floor(A_SHARE, 99) == 0, "不足一手不发单"

    def test_us_stock_keeps_odd_lots(self):
        assert lot_floor(US_STOCK, 137.9) == 137
        assert lot_floor(US_STOCK, 0.5) == 0

    def test_never_rounds_up(self):
        """向上取整会让下单量超过可用资金 → 成交被券商拒。"""
        for m in (A_SHARE, US_STOCK, CRYPTO):
            for raw in (1.9, 99.99, 100.01, 1000.5):
                assert lot_floor(m, raw) <= raw


class TestTPlusOne:
    def test_a_share_freezes_todays_buys(self):
        # 持有 1000，其中 300 是今天买的 → 今天最多卖 700
        assert can_sell_today(A_SHARE, bought_today_qty=300, holding_qty=1000) == 700

    def test_us_stock_can_sell_everything_same_day(self):
        assert can_sell_today(US_STOCK, bought_today_qty=300, holding_qty=1000) == 1000

    def test_paper_broker_honors_t_plus_one(self):
        """🔴 `PaperBroker` 此前明写「没有 T+1 限制」。

        那会让纸面成绩**系统性优于实盘**（纸面能当天来回做 T），
        而「新策略先 paper 跑一段再上实盘」这条护栏正是靠纸面成绩判断的。
        """
        from trading_engine.brokers.paper_broker import PaperBroker

        b = PaperBroker(initial_cash=1_000_000)
        b.update_market_price("600519.SH", 100.0)
        buy = b.submit_order("600519.SH", "BUY", 1000, 100.0)
        assert buy.status.value in ("FILLED", "filled"), buy.error_msg

        same_day = b.submit_order("600519.SH", "SELL", 1000, 100.0)
        assert same_day.status.value not in ("FILLED", "filled"), "A 股当天买的不能当天卖"

        b.settle_t1()          # 次日开盘
        next_day = b.submit_order("600519.SH", "SELL", 1000, 100.0)
        assert next_day.status.value in ("FILLED", "filled"), next_day.error_msg

    def test_paper_broker_lets_us_stock_trade_same_day(self):
        """⛔ 别把 A 股的 T+1 套到美股头上。"""
        from trading_engine.brokers.paper_broker import PaperBroker

        b = PaperBroker(initial_cash=1_000_000)
        b.update_market_price("AAPL", 200.0)
        b.submit_order("AAPL", "BUY", 100, 200.0)
        same_day = b.submit_order("AAPL", "SELL", 100, 200.0)
        assert same_day.status.value in ("FILLED", "filled"), "美股 T+0，当天就能卖"


class TestPriceLimit:
    def test_limit_up_blocks_buy_but_not_sell(self):
        ok, why = is_tradable_at(A_SHARE, "600519.SH", 10.0, "BUY")
        assert ok is False and "涨停" in why
        ok2, _ = is_tradable_at(A_SHARE, "600519.SH", 10.0, "SELL")
        assert ok2 is True, "涨停可以卖"

    def test_limit_down_blocks_sell_but_not_buy(self):
        ok, why = is_tradable_at(A_SHARE, "600519.SH", -10.0, "SELL")
        assert ok is False and "跌停" in why
        ok2, _ = is_tradable_at(A_SHARE, "600519.SH", -10.0, "BUY")
        assert ok2 is True, "跌停可以买"

    def test_us_stock_has_no_price_limit(self):
        """美股涨 30% 也照买 —— ⛔ 别拿 A 股的 10% 去卡它。"""
        ok, _ = is_tradable_at(US_STOCK, "AAPL", 30.0, "BUY")
        assert ok is True

    def test_missing_change_pct_does_not_block(self):
        """取不到涨跌幅时放行 —— ⛔ 别把「不知道」当成「封板了」，
        那会让整条策略在数据缺失时静默停摆。"""
        ok, _ = is_tradable_at(A_SHARE, "600519.SH", None, "BUY")
        assert ok is True


def test_lot_size_matches_cpp_engine():
    """🔒 与 C++ 回测侧的 `MarketRules` 必须一致。

    不一致的后果是「回测能成交、实盘成交不了」，而那种偏差会被当成策略问题查很久。
    """
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[2] / \
        "backtest_cpp/include/backtest/types.h"
    text = src.read_text(encoding="utf-8")
    cpp = dict(re.findall(r"static MarketRules (\w+)\(\)\s*\{\s*return \{(\d+)\};", text))
    assert cpp, "没在 types.h 里解析到 MarketRules（重构过？）"

    for market, key in ((A_SHARE, "a_share"), (US_STOCK, "us_stock"),
                        (HK_STOCK, "hk_stock"), (CRYPTO, "crypto")):
        assert int(cpp[key]) == rules_for(market).lot_size, (
            f"{market} 的一手股数两边对不上：C++={cpp[key]}、"
            f"Python={rules_for(market).lot_size}")
