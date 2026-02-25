/*
 * test_matching.cpp — MatchingEngine 的单元测试
 *
 * 测试四种订单类型的撮合逻辑：MARKET, LIMIT, IOC, FOK
 */

#include <gtest/gtest.h>
#include "orderbook/matching_engine.h"

using namespace orderbook;

// 辅助：创建一个有基本盘口的订单簿
//   asks: 101.00 (150), 101.50 (200), 102.00 (100)
//   bids: 100.50 (100), 100.00 (250)
static LimitOrderBook make_standard_book() {
    LimitOrderBook book;

    auto add = [&](const std::string& id, Side side, double price, int qty) {
        BookOrder o;
        o.order_id = id;
        o.side = side;
        o.order_type = OrderType::LIMIT;
        o.price = price;
        o.quantity = qty;
        o.timestamp = now_ns();
        book.add_order(std::move(o));
    };

    // 卖盘
    add("a1", Side::SELL, 101.00, 150);
    add("a2", Side::SELL, 101.50, 200);
    add("a3", Side::SELL, 102.00, 100);

    // 买盘
    add("b1", Side::BUY, 100.50, 100);
    add("b2", Side::BUY, 100.00, 250);

    return book;
}


// ============================================================
// MARKET 市价单测试
// ============================================================

TEST(MatchingTest, MarketBuy_PartialFill) {
    // 市价买入 100 股 → 吃掉 101.00 上的 100 股
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::MARKET;
    order.quantity = 100;

    auto result = engine.submit_order(book, order);

    EXPECT_FALSE(result.is_rejected);
    EXPECT_EQ(result.filled_quantity, 100);
    EXPECT_EQ(result.fills.size(), 1);
    EXPECT_EQ(result.fills[0].price, 101.00);
    EXPECT_EQ(result.fills[0].quantity, 100);

    // 101.00 还剩 50 股
    EXPECT_EQ(book.ask_quantity_at(101.00), 50);
}

TEST(MatchingTest, MarketBuy_CrossMultipleLevels) {
    // 市价买入 200 股 → 吃完 101.00 (150) + 吃 101.50 (50)
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::MARKET;
    order.quantity = 200;

    auto result = engine.submit_order(book, order);

    EXPECT_EQ(result.filled_quantity, 200);
    EXPECT_EQ(result.fills.size(), 2);
    EXPECT_EQ(result.fills[0].price, 101.00);
    EXPECT_EQ(result.fills[0].quantity, 150);
    EXPECT_EQ(result.fills[1].price, 101.50);
    EXPECT_EQ(result.fills[1].quantity, 50);
}

TEST(MatchingTest, MarketSell) {
    // 市价卖出 80 股 → 吃掉 100.50 上的 80 股
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::SELL;
    order.order_type = OrderType::MARKET;
    order.quantity = 80;

    auto result = engine.submit_order(book, order);

    EXPECT_EQ(result.filled_quantity, 80);
    EXPECT_EQ(result.fills[0].price, 100.50);   // 从最高买价开始吃
}


// ============================================================
// LIMIT 限价单测试
// ============================================================

TEST(MatchingTest, LimitBuy_NoMatch) {
    // 限价买入 @ 100.50 → 低于 best_ask(101.00)，无法成交 → 挂单
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::LIMIT;
    order.price = 100.50;
    order.quantity = 200;

    auto result = engine.submit_order(book, order);

    EXPECT_EQ(result.filled_quantity, 0);
    EXPECT_TRUE(result.is_resting);    // 挂在订单簿上了
    EXPECT_EQ(result.fills.size(), 0);

    // 100.50 上原来有 100 股，加上新挂的 200 股 = 300
    EXPECT_EQ(book.bid_quantity_at(100.50), 300);
}

TEST(MatchingTest, LimitBuy_PartialMatchThenRest) {
    // 限价买入 300 股 @ 101.00
    // 先成交 101.00 的 150 股（刚好全吃完该价位）
    // 剩余 150 股挂在 101.00 的买盘上
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::LIMIT;
    order.price = 101.00;
    order.quantity = 300;

    auto result = engine.submit_order(book, order);

    EXPECT_EQ(result.filled_quantity, 150);
    EXPECT_EQ(result.remaining_quantity, 150);
    EXPECT_TRUE(result.is_resting);

    // 新的 best_bid 应该是 101.00（刚挂的 150 股）
    EXPECT_EQ(book.best_bid().value(), 101.00);
    EXPECT_EQ(book.bid_quantity_at(101.00), 150);
}

TEST(MatchingTest, LimitBuy_FullMatch) {
    // 限价买入 150 股 @ 102.00 → 刚好吃完 101.00 的 150 股
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::LIMIT;
    order.price = 102.00;
    order.quantity = 150;

    auto result = engine.submit_order(book, order);

    EXPECT_EQ(result.filled_quantity, 150);
    EXPECT_EQ(result.remaining_quantity, 0);
    EXPECT_FALSE(result.is_resting);
}


// ============================================================
// IOC (Immediate or Cancel) 测试
// ============================================================

TEST(MatchingTest, IOC_PartialFillThenCancel) {
    // IOC 买入 300 股 @ 101.00
    // 只能成交 150 股（101.00 上的全部），剩余 150 股取消
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::IOC;
    order.price = 101.00;
    order.quantity = 300;

    auto result = engine.submit_order(book, order);

    EXPECT_EQ(result.filled_quantity, 150);
    EXPECT_EQ(result.remaining_quantity, 150);
    EXPECT_FALSE(result.is_resting);    // IOC 不挂单！
    EXPECT_FALSE(result.is_rejected);
}

TEST(MatchingTest, IOC_NoMatch) {
    // IOC 买入 @ 100.00 → 低于 best_ask → 无法成交 → 全部取消
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::IOC;
    order.price = 100.00;
    order.quantity = 100;

    auto result = engine.submit_order(book, order);

    EXPECT_EQ(result.filled_quantity, 0);
    EXPECT_FALSE(result.is_resting);
}


// ============================================================
// FOK (Fill or Kill) 测试
// ============================================================

TEST(MatchingTest, FOK_Reject) {
    // FOK 买入 200 股 @ 101.00
    // 但 101.00 只有 150 股 → 不够 → 整单拒绝
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::FOK;
    order.price = 101.00;
    order.quantity = 200;

    auto result = engine.submit_order(book, order);

    EXPECT_TRUE(result.is_rejected);
    EXPECT_EQ(result.filled_quantity, 0);
    EXPECT_EQ(result.fills.size(), 0);

    // 订单簿不应该有任何变化
    EXPECT_EQ(book.ask_quantity_at(101.00), 150);
}

TEST(MatchingTest, FOK_FullFill) {
    // FOK 买入 150 股 @ 101.00 → 刚好够 → 全部成交
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::FOK;
    order.price = 101.00;
    order.quantity = 150;

    auto result = engine.submit_order(book, order);

    EXPECT_FALSE(result.is_rejected);
    EXPECT_EQ(result.filled_quantity, 150);
}

TEST(MatchingTest, FOK_MultiLevel) {
    // FOK 买入 300 股 @ 101.50
    // 101.00 有 150 + 101.50 有 200 = 350 ≥ 300 → 可以成交
    auto book = make_standard_book();
    MatchingEngine engine;

    BookOrder order;
    order.side = Side::BUY;
    order.order_type = OrderType::FOK;
    order.price = 101.50;
    order.quantity = 300;

    auto result = engine.submit_order(book, order);

    EXPECT_FALSE(result.is_rejected);
    EXPECT_EQ(result.filled_quantity, 300);
    EXPECT_EQ(result.fills.size(), 2);   // 跨两个价位
}
