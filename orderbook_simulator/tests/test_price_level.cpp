/*
 * test_price_level.cpp — PriceLevel 的单元测试
 *
 * 使用 Google Test 框架。
 * 测试写法：
 *   TEST(测试套件名, 测试用例名) {
 *       // 准备数据
 *       // 执行操作
 *       // 用 EXPECT_xxx 检查结果
 *   }
 *
 * 常用断言：
 *   EXPECT_EQ(a, b)    — 期望 a == b
 *   EXPECT_TRUE(x)     — 期望 x 为 true
 *   EXPECT_FALSE(x)    — 期望 x 为 false
 *   EXPECT_GT(a, b)    — 期望 a > b (Greater Than)
 */

#include <gtest/gtest.h>
#include "orderbook/price_level.h"

using namespace orderbook;

// ── 基本功能测试 ──

TEST(PriceLevelTest, EmptyLevel) {
    PriceLevel level(100.0);

    EXPECT_EQ(level.price(), 100.0);
    EXPECT_TRUE(level.is_empty());
    EXPECT_EQ(level.total_quantity(), 0);
    EXPECT_EQ(level.order_count(), 0);
}

TEST(PriceLevelTest, AddOrder) {
    PriceLevel level(100.0);

    BookOrder order;
    order.order_id = "o1";
    order.side = Side::BUY;
    order.price = 100.0;
    order.quantity = 500;

    level.add_order(order);

    EXPECT_FALSE(level.is_empty());
    EXPECT_EQ(level.total_quantity(), 500);
    EXPECT_EQ(level.order_count(), 1);
}

TEST(PriceLevelTest, MultipleOrders) {
    PriceLevel level(100.0);

    for (int i = 0; i < 3; i++) {
        BookOrder order;
        order.order_id = "o" + std::to_string(i);
        order.side = Side::BUY;
        order.price = 100.0;
        order.quantity = 100 * (i + 1);   // 100, 200, 300
        level.add_order(order);
    }

    EXPECT_EQ(level.order_count(), 3);
    EXPECT_EQ(level.total_quantity(), 600);   // 100 + 200 + 300
}


// ── 撤单测试 ──

TEST(PriceLevelTest, RemoveOrder) {
    PriceLevel level(100.0);

    BookOrder order;
    order.order_id = "o1";
    order.quantity = 500;
    level.add_order(order);

    EXPECT_TRUE(level.remove_order("o1"));
    EXPECT_TRUE(level.is_empty());
    EXPECT_EQ(level.total_quantity(), 0);
}

TEST(PriceLevelTest, RemoveNonexistent) {
    PriceLevel level(100.0);

    BookOrder order;
    order.order_id = "o1";
    order.quantity = 500;
    level.add_order(order);

    // 试图撤销一个不存在的订单
    EXPECT_FALSE(level.remove_order("o999"));
    EXPECT_EQ(level.total_quantity(), 500);   // 原订单不受影响
}


// ── 撮合测试 ──

TEST(PriceLevelTest, MatchPartial) {
    // 场景：价位上有 500 股，来单只要 200 股
    PriceLevel level(100.0);

    BookOrder order;
    order.order_id = "resting1";
    order.side = Side::SELL;
    order.price = 100.0;
    order.quantity = 500;
    level.add_order(order);

    auto [matched, fills] = level.match(200, Side::BUY, "aggressor1");

    EXPECT_EQ(matched, 200);                // 成交了 200 股
    EXPECT_EQ(fills.size(), 1);             // 1 笔成交
    EXPECT_EQ(fills[0].quantity, 200);
    EXPECT_EQ(fills[0].price, 100.0);
    EXPECT_EQ(fills[0].buy_order_id, "aggressor1");
    EXPECT_EQ(fills[0].sell_order_id, "resting1");
    EXPECT_EQ(level.total_quantity(), 300); // 挂单还剩 300
}

TEST(PriceLevelTest, MatchFull) {
    // 场景：价位上有 500 股，来单要 500 股 → 全部成交
    PriceLevel level(100.0);

    BookOrder order;
    order.order_id = "resting1";
    order.side = Side::SELL;
    order.quantity = 500;
    level.add_order(order);

    auto [matched, fills] = level.match(500, Side::BUY, "aggressor1");

    EXPECT_EQ(matched, 500);
    EXPECT_TRUE(level.is_empty());   // 价位清空了
}

TEST(PriceLevelTest, MatchFIFO) {
    // 场景：价位上有两个挂单 (300 + 200 = 500)，来单要 400 股
    // 应该先吃第一个（300 全吃），再吃第二个（吃 100）
    PriceLevel level(100.0);

    BookOrder o1;
    o1.order_id = "first";
    o1.side = Side::SELL;
    o1.quantity = 300;
    level.add_order(o1);

    BookOrder o2;
    o2.order_id = "second";
    o2.side = Side::SELL;
    o2.quantity = 200;
    level.add_order(o2);

    auto [matched, fills] = level.match(400, Side::BUY, "aggressor");

    EXPECT_EQ(matched, 400);
    EXPECT_EQ(fills.size(), 2);              // 两笔成交
    EXPECT_EQ(fills[0].quantity, 300);       // 第一个全吃
    EXPECT_EQ(fills[0].sell_order_id, "first");
    EXPECT_EQ(fills[1].quantity, 100);       // 第二个吃 100
    EXPECT_EQ(fills[1].sell_order_id, "second");
    EXPECT_EQ(level.total_quantity(), 100);  // 第二个还剩 100
}

TEST(PriceLevelTest, MatchMoreThanAvailable) {
    // 场景：来单要 1000 股，但价位上只有 300 股
    PriceLevel level(100.0);

    BookOrder o1;
    o1.order_id = "resting";
    o1.quantity = 300;
    level.add_order(o1);

    auto [matched, fills] = level.match(1000, Side::BUY, "aggressor");

    EXPECT_EQ(matched, 300);   // 只能成交 300
    EXPECT_TRUE(level.is_empty());
}
