/*
 * test_session.cpp — Session 和 SessionManager 的测试
 */

#include <gtest/gtest.h>
#include "orderbook/session.h"
#include "orderbook/session_manager.h"

using namespace orderbook;


// ============================================================
// Session 测试
// ============================================================

TEST(SessionTest, CreateAndSeed) {
    Session session("s1", "AAPL");

    EXPECT_EQ(session.session_id(), "s1");
    EXPECT_EQ(session.symbol(), "AAPL");

    // 播种 100 个订单
    int added = session.seed_orders(100, 100.0);
    EXPECT_EQ(added, 100);

    // 盘口应该有数据了
    auto depth = session.get_depth(10);
    EXPECT_GT(depth.bids.size(), 0);
    EXPECT_GT(depth.asks.size(), 0);
}

TEST(SessionTest, SubmitMarketOrder) {
    Session session("s1", "TEST");
    session.seed_orders(200, 100.0, 0.01, 2, 20, 100, 500);

    // 提交市价买单
    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::MARKET;
    order.quantity = 100;

    auto result = session.submit_order(order);

    EXPECT_GT(result.filled_quantity, 0);            // 应该有成交
    EXPECT_GT(session.get_fills().size(), 0);        // 成交记录不为空
}

TEST(SessionTest, SubmitLimitAndCancel) {
    Session session("s1", "TEST");
    session.seed_orders(200, 100.0, 0.01, 2, 20, 100, 500);

    // 提交一个远离当前价的限价单（不会成交）
    BookOrder order;
    order.order_id = "my_order";
    order.side = Side::BUY;
    order.order_type = OrderType::LIMIT;
    order.price = 90.0;   // 远低于当前价，不会成交
    order.quantity = 100;

    auto result = session.submit_order(order);

    EXPECT_EQ(result.filled_quantity, 0);
    EXPECT_TRUE(result.is_resting);   // 挂在订单簿上

    // 撤单
    EXPECT_TRUE(session.cancel_order("my_order"));
}

TEST(SessionTest, GetStats) {
    Session session("s1", "TEST");
    session.seed_orders(200, 100.0);

    auto stats = session.get_stats();

    EXPECT_GT(stats.bid_depth, 0);
    EXPECT_GT(stats.ask_depth, 0);
    EXPECT_GT(stats.spread, 0);
}

TEST(SessionTest, EstimateImpact) {
    Session session("s1", "TEST");
    session.seed_orders(200, 100.0);

    // 估算冲击：交易 1000 股，波动率 2%，日均量 100 万
    double impact = session.estimate_impact(1000, 0.02, 1000000);
    EXPECT_GT(impact, 0);   // 冲击应该 > 0
}


// ============================================================
// SessionManager 测试
// ============================================================

TEST(SessionManagerTest, CreateAndGet) {
    SessionManager manager;

    std::string sid = manager.create_session("AAPL");
    EXPECT_FALSE(sid.empty());

    Session* session = manager.get_session(sid);
    EXPECT_NE(session, nullptr);
    EXPECT_EQ(session->symbol(), "AAPL");
}

TEST(SessionManagerTest, MultipleSessions) {
    SessionManager manager;

    auto s1 = manager.create_session("AAPL");
    auto s2 = manager.create_session("GOOG");
    auto s3 = manager.create_session("TSLA");

    EXPECT_EQ(manager.session_count(), 3);
    EXPECT_EQ(manager.list_sessions().size(), 3);
}

TEST(SessionManagerTest, GetNonexistent) {
    SessionManager manager;

    Session* session = manager.get_session("nonexistent");
    EXPECT_EQ(session, nullptr);
}

TEST(SessionManagerTest, RemoveSession) {
    SessionManager manager;

    auto sid = manager.create_session("AAPL");
    EXPECT_EQ(manager.session_count(), 1);

    EXPECT_TRUE(manager.remove_session(sid));
    EXPECT_EQ(manager.session_count(), 0);
    EXPECT_EQ(manager.get_session(sid), nullptr);
}
