/*
 * test_metrics.cpp — 绩效指标的单元测试
 */

#include <gtest/gtest.h>
#include "backtest/metrics.h"
#include <cmath>

using namespace backtest;

// 辅助函数：构造一段简单的 equity curve
static std::vector<EquitySnapshot> make_curve(
    const std::vector<double>& values,
    double initial = 100000.0
) {
    std::vector<EquitySnapshot> curve;
    double prev = initial;
    for (size_t i = 0; i < values.size(); ++i) {
        EquitySnapshot snap;
        snap.date = "2025-01-" + std::to_string(i + 1);
        snap.total_value = values[i];
        snap.cash = values[i];
        snap.market_value = 0.0;
        snap.daily_return = (prev > 0) ? (values[i] - prev) / prev : 0.0;
        curve.push_back(snap);
        prev = values[i];
    }
    return curve;
}

// ── 总收益率 ──

TEST(MetricsTest, TotalReturn) {
    auto curve = make_curve({100000, 110000, 120000, 125000});
    double ret = Metrics::calc_total_return(curve, 100000.0);
    EXPECT_NEAR(ret, 0.25, 0.001);   // 25%
}

TEST(MetricsTest, TotalReturnNegative) {
    auto curve = make_curve({100000, 90000, 80000});
    double ret = Metrics::calc_total_return(curve, 100000.0);
    EXPECT_NEAR(ret, -0.20, 0.001);   // -20%
}

TEST(MetricsTest, TotalReturnEmpty) {
    std::vector<EquitySnapshot> empty;
    double ret = Metrics::calc_total_return(empty, 100000.0);
    EXPECT_DOUBLE_EQ(ret, 0.0);
}

// ── 年化收益率 ──

TEST(MetricsTest, AnnualizedReturn) {
    // 252 天赚了 10% → 年化 ≈ 10%
    double ann = Metrics::calc_annualized_return(0.10, 252);
    EXPECT_NEAR(ann, 0.10, 0.001);

    // 126 天赚了 10% → 年化 ≈ 21%（半年翻倍就更多了）
    double ann2 = Metrics::calc_annualized_return(0.10, 126);
    EXPECT_GT(ann2, 0.10);   // 半年赚10%，年化应大于10%
}

// ── 最大回撤 ──

TEST(MetricsTest, MaxDrawdown) {
    // 从 100 涨到 120，再跌到 90，再涨到 110
    auto curve = make_curve({100000, 110000, 120000, 100000, 90000, 95000, 110000});
    auto dd = Metrics::calc_max_drawdown(curve);

    // 从 120000 跌到 90000，回撤 = 30000/120000 = 25%
    EXPECT_NEAR(dd.drawdown_pct, 0.25, 0.001);
    EXPECT_NEAR(dd.drawdown_amount, 30000.0, 1.0);
}

TEST(MetricsTest, NoDrawdown) {
    // 一直涨，没有回撤
    auto curve = make_curve({100000, 105000, 110000, 115000, 120000});
    auto dd = Metrics::calc_max_drawdown(curve);
    EXPECT_DOUBLE_EQ(dd.drawdown_pct, 0.0);
}

// ── 波动率 ──

TEST(MetricsTest, Volatility) {
    // 构造一个有波动的曲线
    auto curve = make_curve({100000, 101000, 99000, 102000, 98000, 103000});
    double vol = Metrics::calc_volatility(curve);
    EXPECT_GT(vol, 0.0);   // 应该有波动率
}

TEST(MetricsTest, ZeroVolatility) {
    // 完全不变的曲线
    auto curve = make_curve({100000, 100000, 100000, 100000});
    double vol = Metrics::calc_volatility(curve);
    EXPECT_DOUBLE_EQ(vol, 0.0);
}

// ── Sharpe 比率 ──

TEST(MetricsTest, SharpeRatio) {
    // 稳定上涨 → 高 Sharpe
    std::vector<double> values;
    double v = 100000;
    for (int i = 0; i < 252; ++i) {
        v *= 1.001;  // 每天涨 0.1%
        values.push_back(v);
    }
    auto curve = make_curve(values);
    double sharpe = Metrics::calc_sharpe_ratio(curve, 0.03);
    EXPECT_GT(sharpe, 1.0);   // 稳定上涨应该有较高的 Sharpe
}

// ── 交易统计 ──

TEST(MetricsTest, TradeStats) {
    std::vector<Fill> fills;

    // 盈利交易：买 100，卖 110
    fills.push_back({"o1", "AAPL", Side::BUY, 100.0, 100, 1.0, 0.0, "2025-01-01", "signal"});
    fills.push_back({"o2", "AAPL", Side::SELL, 110.0, 100, 1.0, 0.0, "2025-01-10", "signal"});

    // 亏损交易：买 100，卖 95
    fills.push_back({"o3", "AAPL", Side::BUY, 100.0, 100, 1.0, 0.0, "2025-02-01", "signal"});
    fills.push_back({"o4", "AAPL", Side::SELL, 95.0, 100, 1.0, 0.0, "2025-02-10", "signal"});

    auto stats = Metrics::calc_trade_stats(fills);

    EXPECT_EQ(stats.total, 2);
    EXPECT_EQ(stats.winners, 1);
    EXPECT_EQ(stats.losers, 1);
    EXPECT_NEAR(stats.win_rate, 0.5, 0.001);
    EXPECT_GT(stats.profit_factor, 0.0);
}

TEST(MetricsTest, TradeStatsEmpty) {
    std::vector<Fill> empty;
    auto stats = Metrics::calc_trade_stats(empty);
    EXPECT_EQ(stats.total, 0);
    EXPECT_DOUBLE_EQ(stats.win_rate, 0.0);
}

// ── 完整 calculate ──

TEST(MetricsTest, CalculateAll) {
    auto curve = make_curve({100000, 105000, 103000, 108000, 106000, 112000});

    std::vector<Fill> fills;
    fills.push_back({"o1", "AAPL", Side::BUY, 100.0, 100, 5.0, 0.0, "2025-01-01", "signal"});
    fills.push_back({"o2", "AAPL", Side::SELL, 112.0, 100, 5.0, 0.0, "2025-01-06", "signal"});

    auto m = Metrics::calculate(curve, fills, 100000.0);

    EXPECT_NEAR(m.total_return, 0.12, 0.001);
    EXPECT_GT(m.volatility, 0.0);
    EXPECT_GT(m.max_drawdown, 0.0);
    EXPECT_EQ(m.total_trades, 1);
    EXPECT_DOUBLE_EQ(m.total_commission, 10.0);
}
