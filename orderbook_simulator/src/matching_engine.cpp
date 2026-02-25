/*
 * matching_engine.cpp — 撮合引擎的具体实现
 */

#include "orderbook/matching_engine.h"
#include <limits>   // std::numeric_limits — 获取类型的最大/最小值

namespace orderbook {

MatchingEngine::MatchingEngine() {}

// ============================================================
// 主入口：根据订单类型分发到不同处理函数
// ============================================================

MatchResult MatchingEngine::submit_order(LimitOrderBook& book, BookOrder order) {
    // 给订单分配 ID 和时间戳（如果还没有的话）
    if (order.order_id.empty()) {
        order.order_id = generate_id();
    }
    if (order.timestamp == 0) {
        order.timestamp = now_ns();
    }

    // 根据订单类型调用不同的处理函数
    // 这就是"策略模式"的简化版：不同类型有不同的处理逻辑
    switch (order.order_type) {
        case OrderType::MARKET:
            return handle_market_order(book, order);
        case OrderType::LIMIT:
            return handle_limit_order(book, order);
        case OrderType::IOC:
            return handle_ioc_order(book, order);
        case OrderType::FOK:
            return handle_fok_order(book, order);
    }

    // 不应该走到这里，但编译器要求所有路径都有返回值
    MatchResult result;
    result.order_id = order.order_id;
    result.is_rejected = true;
    return result;
}


// ============================================================
// 市价单处理
// ============================================================

MatchResult MatchingEngine::handle_market_order(LimitOrderBook& book, BookOrder& order) {
    MatchResult result;
    result.order_id = order.order_id;
    result.is_rejected = false;
    result.is_resting = false;   // 市价单不会挂单

    // price_limit = 0 表示"不限价格"，能吃多少吃多少
    result.fills = match_against_book(book, order, 0.0);

    result.filled_quantity = order.filled_quantity;
    result.remaining_quantity = order.remaining();

    // 市价单不挂单，未成交的部分直接取消
    if (order.remaining() > 0) {
        order.cancel();
    }

    book.cleanup();
    return result;
}


// ============================================================
// 限价单处理
// ============================================================

MatchResult MatchingEngine::handle_limit_order(LimitOrderBook& book, BookOrder& order) {
    MatchResult result;
    result.order_id = order.order_id;
    result.is_rejected = false;

    // 先尝试和对手盘撮合（如果价格能交叉的话）
    result.fills = match_against_book(book, order, order.price);

    result.filled_quantity = order.filled_quantity;
    result.remaining_quantity = order.remaining();

    // 如果还有剩余，把剩余部分挂到订单簿上
    if (order.remaining() > 0 && order.is_active) {
        result.is_resting = true;
        book.add_order(std::move(order));
    } else {
        result.is_resting = false;
    }

    book.cleanup();
    return result;
}


// ============================================================
// IOC 单处理（Immediate or Cancel）
// ============================================================

MatchResult MatchingEngine::handle_ioc_order(LimitOrderBook& book, BookOrder& order) {
    MatchResult result;
    result.order_id = order.order_id;
    result.is_rejected = false;
    result.is_resting = false;   // IOC 绝不挂单

    // 和限价单一样撮合
    result.fills = match_against_book(book, order, order.price);

    result.filled_quantity = order.filled_quantity;
    result.remaining_quantity = order.remaining();

    // 剩余的直接取消（这是 IOC 和 LIMIT 的唯一区别）
    if (order.remaining() > 0) {
        order.cancel();
    }

    book.cleanup();
    return result;
}


// ============================================================
// FOK 单处理（Fill or Kill）
// ============================================================

MatchResult MatchingEngine::handle_fok_order(LimitOrderBook& book, BookOrder& order) {
    MatchResult result;
    result.order_id = order.order_id;
    result.is_resting = false;

    // FOK 的特殊之处：先检查，再成交
    // 如果对手盘的总量不够，整单拒绝（一股都不成交）

    int avail = available_quantity(book, order.side, order.price);

    if (avail < order.quantity) {
        // 数量不够，拒绝整个订单
        result.is_rejected = true;
        result.filled_quantity = 0;
        result.remaining_quantity = order.quantity;
        order.cancel();
        return result;
    }

    // 数量够了，正常撮合（一定能全部成交）
    result.is_rejected = false;
    result.fills = match_against_book(book, order, order.price);
    result.filled_quantity = order.filled_quantity;
    result.remaining_quantity = order.remaining();

    book.cleanup();
    return result;
}


// ============================================================
// 通用撮合逻辑
// ============================================================

std::vector<Fill> MatchingEngine::match_against_book(
    LimitOrderBook& book,
    BookOrder& order,
    double price_limit
) {
    /*
     * 核心撮合循环：
     *
     * 如果来单是 BUY（买入），就去吃 asks（卖盘）：
     *   从最低卖价开始，价格 ≤ price_limit（或不限价）就能成交
     *
     * 如果来单是 SELL（卖出），就去吃 bids（买盘）：
     *   从最高买价开始，价格 ≥ price_limit（或不限价）就能成交
     */

    std::vector<Fill> fills;

    while (order.remaining() > 0) {
        PriceLevel* best_level = nullptr;

        if (order.side == Side::BUY) {
            // 买单吃卖盘，从最低卖价开始
            best_level = book.best_ask_level();
            if (!best_level) break;   // 卖盘空了，没得吃

            // 检查价格是否可以交叉
            // 对于市价单，price_limit = 0，直接跳过价格检查
            if (price_limit > 0 && best_level->price() > price_limit) {
                break;   // 最低卖价已经超过我的出价上限了，停止
            }
        } else {
            // 卖单吃买盘，从最高买价开始
            best_level = book.best_bid_level();
            if (!best_level) break;   // 买盘空了

            if (price_limit > 0 && best_level->price() < price_limit) {
                break;   // 最高买价已经低于我的要价下限了，停止
            }
        }

        // 在这个价位上撮合
        auto [matched, level_fills] = best_level->match(
            order.remaining(),
            order.side,
            order.order_id
        );

        // 更新来单的成交数量
        order.fill(matched);

        // 收集成交记录
        // std::move 把 level_fills 的内容"搬"到 fills 里
        // insert + make_move_iterator 是批量移动 vector 元素的惯用法
        fills.insert(
            fills.end(),
            std::make_move_iterator(level_fills.begin()),
            std::make_move_iterator(level_fills.end())
        );

        // 如果这个价位被吃空了，会在后续 cleanup 中移除
    }

    return fills;
}


// ============================================================
// 检查对手盘可用数量（FOK 用）
// ============================================================

int MatchingEngine::available_quantity(
    const LimitOrderBook& book,
    Side aggressor_side,
    double price_limit
) const {
    /*
     * 遍历对手盘，计算在价格限制范围内的总量。
     * 这个函数只读不写（const），不会修改订单簿。
     *
     * 通过 get_depth 获取盘口数据来计算。
     * 用一个足够大的 levels 值来获取所有价位。
     */

    // 获取盘口快照（100 档应该够了）
    auto depth = book.get_depth(100);
    int total = 0;

    if (aggressor_side == Side::BUY) {
        // 买单检查卖盘
        for (const auto& level : depth.asks) {
            if (price_limit > 0 && level.price > price_limit) break;
            total += level.quantity;
        }
    } else {
        // 卖单检查买盘
        for (const auto& level : depth.bids) {
            if (price_limit > 0 && level.price < price_limit) break;
            total += level.quantity;
        }
    }

    return total;
}

}  // namespace orderbook
