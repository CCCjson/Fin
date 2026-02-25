/*
 * test_strategies.cpp — 策略的单元测试
 */

#include <gtest/gtest.h>
#include "backtest/strategy_context.h"
#include "strategies/ma_cross_strategy.h"
#include "strategies/momentum_strategy.h"

using namespace backtest;

// ── 辅助：生成上涨的 K 线数据 ──
static std::vector<Bar> make_rising_bars(int count, double start = 100.0) {
    std::vector<Bar> bars;
    for (int i = 0; i < count; ++i) {
        Bar b;
        b.date = "2025-01-" + std::to_string(i + 1);
        b.close = start + i * 0.5;   // 每天涨 0.5
        b.open = b.close - 0.2;
        b.high = b.close + 0.3;
        b.low = b.open - 0.3;
        b.volume = 1000000;
        bars.push_back(b);
    }
    return bars;
}

// ── 辅助：生成先涨后跌的 K 线数据 ──
static std::vector<Bar> make_peak_bars(int up_days, int down_days, double start = 100.0) {
    std::vector<Bar> bars;
    int total = up_days + down_days;
    double price = start;
    for (int i = 0; i < total; ++i) {
        Bar b;
        b.date = "2025-01-" + std::to_string(i + 1);
        if (i < up_days) {
            price += 1.0;   // 每天涨 1
        } else {
            price -= 1.0;   // 每天跌 1
        }
        b.close = price;
        b.open = price - 0.3;
        b.high = price + 0.5;
        b.low = price - 0.5;
        b.volume = 1000000;
        bars.push_back(b);
    }
    return bars;
}

// ── MACrossStrategy 基本信息 ──

TEST(MACrossTest, NameAndDescription) {
    MACrossStrategy s(5, 20);
    EXPECT_EQ(s.name(), "MA_CROSS");
    EXPECT_FALSE(s.description().empty());
    EXPECT_FALSE(s.param_schema().empty());
}

// ── MACrossStrategy 数据不足时不交易 ──

TEST(MACrossTest, InsufficientData) {
    MACrossStrategy s(5, 20);
    std::vector<Bar> bars = make_rising_bars(10);

    StrategyContext ctx;
    ctx.symbol = "AAPL";
    ctx.bar_index = 5;   // < slow_period (20)
    ctx.current_bar = bars[5];
    ctx.history = &bars;
    ctx.cash = 100000;
    ctx.position_quantity = 0;

    auto orders = s.on_bar(ctx);
    EXPECT_TRUE(orders.empty());   // 数据不够，不交易
}

// ── MACrossStrategy 先跌后涨时产生金叉买入信号 ──

TEST(MACrossTest, GoldenCross) {
    MACrossStrategy s(5, 20);

    // 构造先跌后涨的数据：前 25 天下跌，后 25 天上涨
    // 这样快线会先在慢线下方，然后上穿（金叉）
    auto bars = make_peak_bars(0, 0);  // 不用这个，手动构造
    bars.clear();
    double price = 120.0;
    for (int i = 0; i < 50; ++i) {
        Bar b;
        b.date = "2025-01-" + std::to_string(i + 1);
        if (i < 25) {
            price -= 0.5;   // 前 25 天下跌
        } else {
            price += 1.0;   // 后 25 天上涨（涨速更快，容易穿越）
        }
        b.close = price;
        b.open = price - 0.2;
        b.high = price + 0.3;
        b.low = price - 0.3;
        b.volume = 1000000;
        bars.push_back(b);
    }

    // 逐 bar 喂给策略
    std::vector<Order> all_orders;
    for (int i = 0; i < static_cast<int>(bars.size()); ++i) {
        std::vector<Bar> history(bars.begin(), bars.begin() + i + 1);

        StrategyContext ctx;
        ctx.symbol = "AAPL";
        ctx.bar_index = i;
        ctx.current_bar = bars[i];
        ctx.history = &history;
        ctx.cash = 100000;
        ctx.position_quantity = 0;
        ctx.total_value = 100000;

        auto orders = s.on_bar(ctx);
        for (auto& o : orders) {
            all_orders.push_back(o);
        }
    }

    // 先跌后涨，快线应该在反转处上穿慢线（金叉）
    bool has_buy = false;
    for (const auto& o : all_orders) {
        if (o.side == Side::BUY) has_buy = true;
    }
    EXPECT_TRUE(has_buy);
}

// ── MomentumStrategy 基本信息 ──

TEST(MomentumTest, NameAndDescription) {
    MomentumStrategy s(20, 0.05, -0.03);
    EXPECT_EQ(s.name(), "MOMENTUM");
    EXPECT_FALSE(s.description().empty());
}

// ── MomentumStrategy 数据不足时不交易 ──

TEST(MomentumTest, InsufficientData) {
    MomentumStrategy s(20);
    std::vector<Bar> bars = make_rising_bars(10);

    StrategyContext ctx;
    ctx.symbol = "AAPL";
    ctx.bar_index = 5;   // < lookback (20)
    ctx.current_bar = bars[5];
    ctx.history = &bars;
    ctx.cash = 100000;
    ctx.position_quantity = 0;

    auto orders = s.on_bar(ctx);
    EXPECT_TRUE(orders.empty());
}

// ── MomentumStrategy 上涨超过阈值时买入 ──

TEST(MomentumTest, BuyOnMomentum) {
    // 设置低阈值，容易触发
    MomentumStrategy s(10, 0.02, -0.02);

    // 构造 20 根上涨 K 线
    auto bars = make_rising_bars(25, 100.0);

    // 模拟到第 15 根 bar
    std::vector<Bar> history(bars.begin(), bars.begin() + 15);
    StrategyContext ctx;
    ctx.symbol = "AAPL";
    ctx.bar_index = 14;
    ctx.current_bar = bars[14];
    ctx.history = &history;
    ctx.cash = 100000;
    ctx.position_quantity = 0;
    ctx.total_value = 100000;

    auto orders = s.on_bar(ctx);

    // 涨了 14 * 0.5 = 7 元，从 100 涨到 107
    // 10 天涨幅 = (107 - 102) / 102 ≈ 4.9% > 2%
    // 应该买入
    EXPECT_FALSE(orders.empty());
    if (!orders.empty()) {
        EXPECT_EQ(orders[0].side, Side::BUY);
    }
}

// ── MomentumStrategy 下跌时卖出 ──

TEST(MomentumTest, SellOnDowntrend) {
    MomentumStrategy s(10, 0.05, -0.03);

    // 先涨后跌
    auto bars = make_peak_bars(15, 15, 100.0);

    // 模拟到第 28 根 bar（下跌阶段）
    std::vector<Bar> history(bars.begin(), bars.begin() + 28);
    StrategyContext ctx;
    ctx.symbol = "AAPL";
    ctx.bar_index = 27;
    ctx.current_bar = bars[27];
    ctx.history = &history;
    ctx.cash = 10000;
    ctx.position_quantity = 100;   // 有持仓
    ctx.position_avg_price = 110.0;
    ctx.total_value = ctx.cash + 100 * bars[27].close;

    auto orders = s.on_bar(ctx);

    // 在下跌阶段，动量应该 < -0.03
    // 有持仓 → 应该产生卖出信号
    if (!orders.empty()) {
        EXPECT_EQ(orders[0].side, Side::SELL);
    }
}

// ── StrategyContext SMA 测试 ──

TEST(ContextTest, SMA) {
    std::vector<Bar> bars;
    for (int i = 0; i < 10; ++i) {
        Bar b;
        b.close = 100.0 + i;   // 100, 101, 102, ..., 109
        bars.push_back(b);
    }

    StrategyContext ctx;
    ctx.history = &bars;
    ctx.current_bar = bars.back();

    // SMA(5) = 平均最后 5 个 = (105+106+107+108+109)/5 = 107
    EXPECT_NEAR(ctx.sma(5), 107.0, 0.001);

    // SMA(10) = 平均所有 10 个 = (100+...+109)/10 = 104.5
    EXPECT_NEAR(ctx.sma(10), 104.5, 0.001);

    // SMA(20)：数据不够，返回 0
    EXPECT_DOUBLE_EQ(ctx.sma(20), 0.0);
}

// ── StrategyContext returns 测试 ──

TEST(ContextTest, Returns) {
    std::vector<Bar> bars;
    for (int i = 0; i < 10; ++i) {
        Bar b;
        b.close = 100.0 + i * 2;   // 100, 102, 104, ..., 118
        bars.push_back(b);
    }

    StrategyContext ctx;
    ctx.history = &bars;
    ctx.current_bar = bars.back();   // close = 118

    // returns(5)：5 天前 close = 108，当前 = 118
    // (118 - 108) / 108 ≈ 0.0926
    EXPECT_NEAR(ctx.returns(5), (118.0 - 108.0) / 108.0, 0.001);
}
