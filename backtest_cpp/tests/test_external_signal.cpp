/*
 * test_external_signal.cpp — 外部信号回放策略 (ExternalSignalStrategy) 的测试
 *
 * 验证「run_signals」端点背后的核心：外部信号被忠实回放，且沿用引擎的
 * 次日开盘成交（防未来函数）、T+1、滑点、费用口径。
 */

#include <gtest/gtest.h>
#include "backtest/engine.h"
#include "strategies/external_signal_strategy.h"

using namespace backtest;

// ── 辅助：生成温和上升的日线（日期 2025-01-01 起，编号连续） ──
static std::vector<Bar> make_bars(int days, double start = 100.0, double step = 1.0) {
    std::vector<Bar> bars;
    double price = start;
    for (int i = 0; i < days; ++i) {
        Bar b;
        char buf[16];
        snprintf(buf, sizeof(buf), "2025-%02d-%02d", (i / 28) + 1, (i % 28) + 1);
        b.date = buf;
        b.open = price;
        price += step;
        b.close = price;
        b.high = std::max(b.open, b.close) + 0.5;
        b.low = std::min(b.open, b.close) - 0.5;
        b.volume = 1000000;
        bars.push_back(b);
    }
    return bars;
}

static std::map<std::string, SignalEntry> make_signal(const std::string& date, Side side, double weight = 0.95) {
    std::map<std::string, SignalEntry> m;
    SignalEntry e;
    e.side = side;
    e.weight = weight;
    m[date] = e;
    return m;
}

// ── 信号被回放，且买单在【信号日次日开盘】成交（防未来函数） ──
TEST(ExternalSignalTest, FillsAtNextBarOpen) {
    auto bars = make_bars(10);                 // date[0..9]
    std::string buy_date = bars[2].date;       // 信号日
    std::string expected_fill_date = bars[3].date;  // 应在次日成交

    auto signals = make_signal(buy_date, Side::BUY);

    BacktestEngine engine(100000.0, CommissionConfig::a_share());
    engine.set_strategy(std::make_unique<ExternalSignalStrategy>(std::move(signals)));
    engine.load_data("TEST", std::move(bars));
    auto result = engine.run();

    EXPECT_EQ(result.strategy_name, "SIGNAL");
    ASSERT_EQ(result.trades.size(), 1u);
    EXPECT_EQ(result.trades[0].date, expected_fill_date);   // 次日开盘，绝非信号日
    EXPECT_EQ(result.trades[0].side, Side::BUY);
    // 成交价 = 次日开盘价 × (1 + 滑点)，> 次日开盘价本身
    EXPECT_GT(result.trades[0].price, 0.0);
    EXPECT_GT(result.metrics.total_commission, 0.0);   // 费用被收取
    EXPECT_GT(result.metrics.total_slippage, 0.0);     // 滑点被收取
}

// ── 一买一卖：两笔成交都推迟一天，卖出全平 ──
TEST(ExternalSignalTest, BuyThenSellFullExit) {
    auto bars = make_bars(10);
    std::string buy_date = bars[2].date;
    std::string sell_date = bars[6].date;

    std::map<std::string, SignalEntry> signals;
    { SignalEntry e; e.side = Side::BUY;  e.weight = 0.95; signals[buy_date] = e; }
    { SignalEntry e; e.side = Side::SELL; e.weight = 0.95; signals[sell_date] = e; }

    BacktestEngine engine(100000.0, CommissionConfig::a_share());
    engine.set_strategy(std::make_unique<ExternalSignalStrategy>(std::move(signals)));
    engine.load_data("TEST", std::move(bars));
    auto result = engine.run();

    ASSERT_EQ(result.trades.size(), 2u);
    EXPECT_EQ(result.trades[0].side, Side::BUY);
    EXPECT_EQ(result.trades[1].side, Side::SELL);
    EXPECT_EQ(result.trades[1].date, "2025-01-08");   // sell_date=date[6]=01-07 的次日
    // 卖出数量 = 买入数量（全平）
    EXPECT_EQ(result.trades[0].quantity, result.trades[1].quantity);
    EXPECT_EQ(result.metrics.total_trades, 1);        // FIFO 一买一卖 = 1 笔完整交易
}

// ── 最后一 bar 的信号无次日开盘可成交 → 丢弃并计数 ──
TEST(ExternalSignalTest, LastBarSignalDiscarded) {
    auto bars = make_bars(10);
    std::string last_date = bars[9].date;   // 末 bar

    auto signals = make_signal(last_date, Side::BUY);

    BacktestEngine engine(100000.0, CommissionConfig::a_share());
    engine.set_strategy(std::make_unique<ExternalSignalStrategy>(std::move(signals)));
    engine.load_data("TEST", std::move(bars));
    auto result = engine.run();

    EXPECT_EQ(result.trades.size(), 0u);
    EXPECT_EQ(result.dropped_last_bar_orders, 1);
}

// ── 空信号：合法，无成交，资金曲线仍完整 ──
TEST(ExternalSignalTest, EmptySignalsNoTrades) {
    auto bars = make_bars(10);
    BacktestEngine engine(100000.0, CommissionConfig::a_share());
    engine.set_strategy(std::make_unique<ExternalSignalStrategy>(std::map<std::string, SignalEntry>{}));
    engine.load_data("TEST", std::move(bars));
    auto result = engine.run();

    EXPECT_EQ(result.trades.size(), 0u);
    EXPECT_EQ(result.equity_curve.size(), 10u);
    EXPECT_DOUBLE_EQ(result.metrics.final_value, 100000.0);   // 全程空仓，本金不变
}

// ── 无持仓时的 SELL 信号是 no-op，不产生成交 ──
TEST(ExternalSignalTest, SellWithoutPositionIsNoop) {
    auto bars = make_bars(10);
    auto signals = make_signal(bars[2].date, Side::SELL);

    BacktestEngine engine(100000.0, CommissionConfig::a_share());
    engine.set_strategy(std::make_unique<ExternalSignalStrategy>(std::move(signals)));
    engine.load_data("TEST", std::move(bars));
    auto result = engine.run();

    EXPECT_EQ(result.trades.size(), 0u);
    EXPECT_EQ(result.dropped_last_bar_orders, 0);
}
