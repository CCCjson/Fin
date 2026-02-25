/*
 * session.cpp — 会话管理的实现
 */

#include "orderbook/session.h"
#include <random>    // 随机数生成器（C++11 标准库）
#include <algorithm> // std::min, std::max

namespace orderbook {

Session::Session(const std::string& session_id, const std::string& symbol)
    : session_id_(session_id)
    , symbol_(symbol)
    // book_ 和 engine_ 使用默认构造函数自动初始化
{
}


// ============================================================
// 查询方法
// ============================================================

const std::string& Session::session_id() const {
    return session_id_;
}

const std::string& Session::symbol() const {
    return symbol_;
}

DepthSnapshot Session::get_depth(int levels) const {
    return book_.get_depth(levels);
}

const std::vector<Fill>& Session::get_fills() const {
    // 返回 const 引用：调用者可以读但不能改
    return all_fills_;
}

std::vector<Fill> Session::get_recent_fills(int limit) const {
    // 取最后 N 笔成交
    if (limit <= 0 || all_fills_.empty()) {
        return {};
    }

    int count = std::min(limit, static_cast<int>(all_fills_.size()));
    // 从 vector 尾部取 count 个元素
    // end() - count 指向倒数第 count 个位置
    return std::vector<Fill>(all_fills_.end() - count, all_fills_.end());
}

BookStats Session::get_stats(int depth_levels) const {
    return Statistics::calculate(book_, all_fills_, depth_levels);
}

double Session::estimate_impact(int quantity, double volatility, double daily_volume) const {
    auto mp = book_.mid_price();
    if (!mp) return 0.0;

    return MarketImpact::estimate_impact_price(
        *mp, volatility, quantity, daily_volume
    );
}

std::optional<BookOrder> Session::find_order(const std::string& order_id) const {
    return book_.find_order(order_id);
}


// ============================================================
// 操作方法
// ============================================================

MatchResult Session::submit_order(BookOrder order) {
    auto result = engine_.submit_order(book_, std::move(order));

    // 把新的成交记录追加到历史列表
    for (auto& fill : result.fills) {
        all_fills_.push_back(fill);
    }

    return result;
}

bool Session::cancel_order(const std::string& order_id) {
    return book_.cancel_order(order_id);
}


// ============================================================
// 播种随机订单
// ============================================================

int Session::seed_orders(
    int count,
    double mid_price,
    double tick_size,
    int spread_ticks,
    int depth_ticks,
    int min_qty,
    int max_qty
) {
    /*
     * 播种就是生成一堆随机的限价单，让订单簿看起来像真实的盘口。
     *
     * 策略：
     *   - 买单价格 < mid_price（在中间价下方）
     *   - 卖单价格 > mid_price（在中间价上方）
     *   - 越靠近中间价的价位，挂单量越大（模拟真实盘口的形状）
     *   - 数量在 [min_qty, max_qty] 之间随机
     */

    // ── C++ 随机数生成 ──
    // C++ 的随机数比 rand() 更好用更公平
    // std::mt19937 是梅森旋转算法，是目前最常用的随机数引擎
    // std::random_device 用来获取一个真随机种子
    std::random_device rd;        // 真随机数（用于播种，很慢）
    std::mt19937 rng(rd());       // 伪随机数引擎（用真随机数播种，之后很快）

    // 均匀分布：在 [a, b] 范围内等概率取值
    std::uniform_int_distribution<int> qty_dist(min_qty, max_qty);
    std::uniform_int_distribution<int> side_dist(0, 1);   // 0=买，1=卖

    int half_spread = spread_ticks / 2;
    // 买盘最高价 = mid_price - half_spread × tick_size
    // 卖盘最低价 = mid_price + half_spread × tick_size
    double bid_top = mid_price - half_spread * tick_size;
    double ask_bottom = mid_price + (half_spread + 1) * tick_size;

    int added = 0;

    for (int i = 0; i < count; i++) {
        BookOrder order;
        order.order_id = generate_id();
        order.order_type = OrderType::LIMIT;
        order.timestamp = now_ns();
        order.client_tag = "seed";   // 标记为系统播种的，不是用户下的

        // 随机决定买还是卖
        if (side_dist(rng) == 0) {
            // 买单
            order.side = Side::BUY;
            // 在 [bid_top - depth_ticks * tick_size, bid_top] 范围内随机一个价格
            std::uniform_int_distribution<int> tick_dist(0, depth_ticks);
            int ticks_below = tick_dist(rng);
            order.price = bid_top - ticks_below * tick_size;
            // 四舍五入到 tick_size 精度
            order.price = std::round(order.price / tick_size) * tick_size;
        } else {
            // 卖单
            order.side = Side::SELL;
            std::uniform_int_distribution<int> tick_dist(0, depth_ticks);
            int ticks_above = tick_dist(rng);
            order.price = ask_bottom + ticks_above * tick_size;
            order.price = std::round(order.price / tick_size) * tick_size;
        }

        // 随机数量
        order.quantity = qty_dist(rng);

        // 直接加到订单簿（不经过撮合，因为买单价格一定 < 卖单价格）
        book_.add_order(std::move(order));
        added++;
    }

    return added;
}

}  // namespace orderbook
