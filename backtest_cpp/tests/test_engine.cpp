/*
 * test_engine.cpp — 回测引擎的集成测试
 *
 * 这些测试验证整个回测流程的正确性：
 * 数据加载 → 策略运行 → 订单执行 → 绩效计算
 */

#include <gtest/gtest.h>
#include "backtest/engine.h"
#include "backtest/data_loader.h"
#include "strategies/ma_cross_strategy.h"
#include "strategies/momentum_strategy.h"

using namespace backtest;

// ── 辅助：生成趋势数据 ──
static std::vector<Bar> make_trend_data(int days, double start, double daily_change) {
    std::vector<Bar> bars;
    double price = start;
    for (int i = 0; i < days; ++i) {
        Bar b;
        char buf[16];
        snprintf(buf, sizeof(buf), "2025-%02d-%02d", (i / 28) + 1, (i % 28) + 1);
        b.date = buf;
        b.open = price;
        price += daily_change;
        b.close = price;
        b.high = std::max(b.open, b.close) + 0.5;
        b.low = std::min(b.open, b.close) - 0.5;
        b.volume = 1000000;
        bars.push_back(b);
    }
    return bars;
}

// ── 基本运行测试 ──

TEST(EngineTest, BasicRun) {
    auto bars = DataLoader::generate_sample_data(100);

    BacktestEngine engine(100000.0, CommissionConfig::us_stock());
    engine.set_strategy(std::make_unique<MACrossStrategy>(5, 20));
    engine.load_data("TEST", std::move(bars));

    auto result = engine.run();

    EXPECT_EQ(result.symbol, "TEST");
    EXPECT_EQ(result.strategy_name, "MA_CROSS");
    EXPECT_EQ(result.equity_curve.size(), 100);
    EXPECT_GT(result.metrics.final_value, 0);
}

// ── 无策略时抛异常 ──

TEST(EngineTest, NoStrategyThrows) {
    BacktestEngine engine;
    engine.load_data("TEST", DataLoader::generate_sample_data(50));

    EXPECT_THROW(engine.run(), std::runtime_error);
}

// ── 无数据时抛异常 ──

TEST(EngineTest, NoDataThrows) {
    BacktestEngine engine;
    engine.set_strategy(std::make_unique<MACrossStrategy>());

    EXPECT_THROW(engine.run(), std::runtime_error);
}

// ── 先跌后涨时 MA_CROSS 应该捕获到金叉并盈利 ──

TEST(EngineTest, MACrossProfitOnUptrend) {
    // 构造先跌后涨的数据：前 30 天下跌，后 70 天上涨
    // 这样才会产生金叉（快线上穿慢线）
    std::vector<Bar> bars;
    double price = 120.0;
    for (int i = 0; i < 100; ++i) {
        Bar b;
        char buf[16];
        snprintf(buf, sizeof(buf), "2025-%02d-%02d", (i / 28) + 1, (i % 28) + 1);
        b.date = buf;
        b.open = price;
        if (i < 30) {
            price -= 0.3;   // 前 30 天下跌
        } else {
            price += 0.5;   // 后 70 天上涨
        }
        b.close = price;
        b.high = std::max(b.open, b.close) + 0.5;
        b.low = std::min(b.open, b.close) - 0.5;
        b.volume = 1000000;
        bars.push_back(b);
    }

    BacktestEngine engine(100000.0, CommissionConfig{0.0, 0.0, false, 0.0});
    engine.set_strategy(std::make_unique<MACrossStrategy>(5, 20));
    engine.load_data("TEST", std::move(bars));

    auto result = engine.run();

    // 策略应该在反转后买入并在上涨中盈利
    EXPECT_GT(result.metrics.final_value, 100000.0);
    EXPECT_GT(result.metrics.total_return, 0.0);
}

// ── Momentum 策略基本运行 ──

TEST(EngineTest, MomentumBasicRun) {
    auto bars = DataLoader::generate_sample_data(100);

    BacktestEngine engine(100000.0);
    engine.set_strategy(std::make_unique<MomentumStrategy>(10, 0.03, -0.02));
    engine.load_data("TEST", std::move(bars));

    auto result = engine.run();

    EXPECT_EQ(result.strategy_name, "MOMENTUM");
    EXPECT_EQ(result.equity_curve.size(), 100);
}

// ── 日期范围过滤 ──

TEST(EngineTest, DateFilter) {
    auto bars = DataLoader::generate_sample_data(100);

    // 记住第 10 天和第 50 天的日期
    std::string start = bars[10].date;
    std::string end = bars[50].date;

    BacktestEngine engine(100000.0);
    engine.set_strategy(std::make_unique<MACrossStrategy>());
    engine.load_data("TEST", std::move(bars));

    auto result = engine.run(start, end);

    // 资金曲线的长度应该少于 100（被过滤了）
    EXPECT_LE(result.equity_curve.size(), 50);
    EXPECT_GT(result.equity_curve.size(), 0);
}

// ── 绩效指标完整性 ──

TEST(EngineTest, MetricsCompleteness) {
    auto bars = DataLoader::generate_sample_data(252);

    BacktestEngine engine(100000.0, CommissionConfig::a_share());
    engine.set_strategy(std::make_unique<MACrossStrategy>(5, 20));
    engine.load_data("TEST", std::move(bars));

    auto result = engine.run();
    auto& m = result.metrics;

    // 检查各项指标有效
    EXPECT_NE(m.final_value, 0.0);
    // volatility 和 max_drawdown 可能为 0（但不太可能）
    // Sharpe 等可能为负，但应该是有限数
    EXPECT_TRUE(std::isfinite(m.sharpe_ratio));
    EXPECT_TRUE(std::isfinite(m.sortino_ratio));
    EXPECT_TRUE(std::isfinite(m.volatility));
}

// ── 成交记录正确性 ──

TEST(EngineTest, TradeRecords) {
    auto bars = make_trend_data(60, 100.0, 0.3);

    BacktestEngine engine(100000.0, CommissionConfig::us_stock());
    engine.set_strategy(std::make_unique<MACrossStrategy>(5, 20));
    engine.load_data("TEST", std::move(bars));

    auto result = engine.run();

    // 检查成交记录格式
    for (const auto& fill : result.trades) {
        EXPECT_FALSE(fill.symbol.empty());
        EXPECT_GT(fill.price, 0.0);
        EXPECT_GT(fill.quantity, 0);
        EXPECT_GE(fill.commission, 0.0);
    }
}

// ── DataLoader 测试 ──

TEST(DataLoaderTest, GenerateSample) {
    auto bars = DataLoader::generate_sample_data(50, 100.0, 0.02);

    EXPECT_EQ(bars.size(), 50);
    for (const auto& b : bars) {
        EXPECT_FALSE(b.date.empty());
        EXPECT_GT(b.close, 0.0);
        EXPECT_GE(b.high, b.low);
        EXPECT_GT(b.volume, 0.0);
    }
}

TEST(DataLoaderTest, JsonRoundTrip) {
    auto bars = DataLoader::generate_sample_data(10);
    auto j = DataLoader::to_json(bars);
    auto bars2 = DataLoader::from_json(j);

    EXPECT_EQ(bars.size(), bars2.size());
    for (size_t i = 0; i < bars.size(); ++i) {
        EXPECT_EQ(bars[i].date, bars2[i].date);
        EXPECT_DOUBLE_EQ(bars[i].close, bars2[i].close);
    }
}
