/*
 * test_portfolio.cpp — Portfolio 类的单元测试
 */

#include <gtest/gtest.h>
#include "backtest/portfolio.h"

using namespace backtest;

// ── 初始化测试 ──

TEST(PortfolioTest, InitialState) {
    Portfolio p(100000.0);

    EXPECT_DOUBLE_EQ(p.get_cash(), 100000.0);
    EXPECT_DOUBLE_EQ(p.get_initial_capital(), 100000.0);
    EXPECT_DOUBLE_EQ(p.get_total_value(), 100000.0);
    EXPECT_DOUBLE_EQ(p.get_market_value(), 0.0);
    EXPECT_EQ(p.get_total_trades(), 0);
    EXPECT_FALSE(p.has_position("AAPL"));
}

// ── 买入测试 ──

TEST(PortfolioTest, BuyOrder) {
    // Use zero-slippage config so price matches exactly
    CommissionConfig comm = CommissionConfig::us_stock();
    comm.slippage_pct = 0.0;
    Portfolio p(100000.0, comm);

    auto fill = p.execute_order(
        Order::market_buy("AAPL", 100),
        150.0,
        "2025-01-15"
    );

    ASSERT_TRUE(fill.has_value());
    EXPECT_EQ(fill->quantity, 100);
    EXPECT_DOUBLE_EQ(fill->price, 150.0);
    EXPECT_GT(fill->commission, 0.0);       // 有手续费

    EXPECT_TRUE(p.has_position("AAPL"));
    EXPECT_EQ(p.get_position_quantity("AAPL"), 100);
    EXPECT_LT(p.get_cash(), 100000.0);      // 现金减少了
}

// ── 买入后卖出 ──

TEST(PortfolioTest, BuyThenSell) {
    Portfolio p(100000.0, CommissionConfig::us_stock());

    // 买入
    p.execute_order(Order::market_buy("AAPL", 100), 150.0, "2025-01-15");
    EXPECT_TRUE(p.has_position("AAPL"));

    // 卖出
    auto fill = p.execute_order(
        Order::market_sell("AAPL", 100),
        160.0,    // 涨了 10 块
        "2025-01-20"
    );

    ASSERT_TRUE(fill.has_value());
    EXPECT_FALSE(p.has_position("AAPL"));
    EXPECT_EQ(p.get_position_quantity("AAPL"), 0);

    // 应该赚了钱（扣除手续费后）
    EXPECT_GT(p.get_cash(), 100000.0 - 100);  // 大致检查
}

// ── 资金不足被拒绝 ──

TEST(PortfolioTest, InsufficientCash) {
    Portfolio p(1000.0);   // 只有 1000 块

    auto fill = p.execute_order(
        Order::market_buy("AAPL", 100),
        150.0,    // 需要 15000
        "2025-01-15"
    );

    EXPECT_FALSE(fill.has_value());    // 应该被拒绝
    EXPECT_FALSE(p.has_position("AAPL"));
    EXPECT_DOUBLE_EQ(p.get_cash(), 1000.0);   // 现金不变
}

// ── 卖出股票不足被拒绝 ──

TEST(PortfolioTest, InsufficientShares) {
    Portfolio p(100000.0);

    auto fill = p.execute_order(
        Order::market_sell("AAPL", 100),
        150.0,
        "2025-01-15"
    );

    EXPECT_FALSE(fill.has_value());    // 没有持仓，不能卖
}

// ── 限价单测试 ──

TEST(PortfolioTest, LimitOrderBuy) {
    Portfolio p(100000.0, CommissionConfig::us_stock());

    // 限价 145，当前价 150 → 不成交
    auto fill1 = p.execute_order(
        Order::limit_buy("AAPL", 100, 145.0),
        150.0,
        "2025-01-15"
    );
    EXPECT_FALSE(fill1.has_value());

    // 限价 155，当前价 150 → 成交
    auto fill2 = p.execute_order(
        Order::limit_buy("AAPL", 100, 155.0),
        150.0,
        "2025-01-16"
    );
    EXPECT_TRUE(fill2.has_value());
}

// ── 平均成本计算 ──

TEST(PortfolioTest, AverageCost) {
    Portfolio p(100000.0, CommissionConfig{0.0, 0.0, false, 0.0});  // 无手续费

    // 第一次买入 100 股 @ 100
    p.execute_order(Order::market_buy("AAPL", 100), 100.0, "2025-01-15");
    EXPECT_DOUBLE_EQ(p.get_position_avg_price("AAPL"), 100.0);

    // 第二次买入 100 股 @ 120
    p.execute_order(Order::market_buy("AAPL", 100), 120.0, "2025-01-16");

    // 平均成本 = (100*100 + 100*120) / 200 = 110
    EXPECT_DOUBLE_EQ(p.get_position_avg_price("AAPL"), 110.0);
    EXPECT_EQ(p.get_position_quantity("AAPL"), 200);
}

// ── 净值记录 ──

TEST(PortfolioTest, EquityCurve) {
    Portfolio p(100000.0);

    p.record_equity("2025-01-15");
    p.record_equity("2025-01-16");

    auto& curve = p.get_equity_curve();
    EXPECT_EQ(curve.size(), 2);
    EXPECT_EQ(curve[0].date, "2025-01-15");
    EXPECT_DOUBLE_EQ(curve[0].total_value, 100000.0);
}

// ── 市值更新 ──

TEST(PortfolioTest, PriceUpdate) {
    Portfolio p(100000.0, CommissionConfig{0.0, 0.0, false, 0.0});

    p.execute_order(Order::market_buy("AAPL", 100), 100.0, "2025-01-15");
    EXPECT_DOUBLE_EQ(p.get_market_value(), 100 * 100.0);

    // 价格涨到 120
    p.update_price("AAPL", 120.0);
    EXPECT_DOUBLE_EQ(p.get_market_value(), 100 * 120.0);
}

// ── A股手续费测试 ──

TEST(PortfolioTest, AShareCommission) {
    CommissionConfig comm = CommissionConfig::a_share();

    // 买入 10 万金额
    double buy_comm = comm.calculate(100000.0, false);
    // 佣金 = max(100000 * 0.00025, 5) = 25 元，无印花税
    EXPECT_DOUBLE_EQ(buy_comm, 25.0);

    // 卖出 10 万金额
    double sell_comm = comm.calculate(100000.0, true);
    // 佣金 25 + 印花税 100000 * 0.001 = 100 → 总计 125
    EXPECT_DOUBLE_EQ(sell_comm, 125.0);

    // 小额交易 1000 元
    double small_comm = comm.calculate(1000.0, false);
    // max(1000 * 0.00025, 5) = max(0.25, 5) = 5（最低佣金）
    EXPECT_DOUBLE_EQ(small_comm, 5.0);
}
