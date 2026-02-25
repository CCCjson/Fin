/*
 * limit_order_book.cpp — 限价订单簿的实现
 */

#include "orderbook/limit_order_book.h"

namespace orderbook {

// ============================================================
// 构造函数
// ============================================================

LimitOrderBook::LimitOrderBook() {
    // bids_ 和 asks_ 是 std::map，自动初始化为空
    // 不需要额外操作
}

// ============================================================
// 查询方法
// ============================================================

std::optional<double> LimitOrderBook::best_bid() const {
    // std::optional 是 C++17 引入的类型，表示"可能有值，也可能没有"
    // 类似 Python 里返回 None 或一个值
    //
    // 用法：
    //   auto bb = book.best_bid();
    //   if (bb.has_value()) { ... 用 bb.value() 或 *bb ... }
    //   或简写：if (bb) { ... 用 *bb ... }

    // 遍历买盘，找到第一个非空的价位
    for (const auto& [price, level] : bids_) {
        // 结构化绑定（C++17）：auto& [price, level] = pair
        // map 的每个元素是 pair<key, value>，这里直接拆成 price 和 level
        if (!level.is_empty()) {
            return price;
        }
    }
    return std::nullopt;   // 买盘为空，没有最优买价
}

std::optional<double> LimitOrderBook::best_ask() const {
    for (const auto& [price, level] : asks_) {
        if (!level.is_empty()) {
            return price;
        }
    }
    return std::nullopt;
}

std::optional<double> LimitOrderBook::spread() const {
    auto bb = best_bid();
    auto ba = best_ask();
    // 两边都有订单才能算价差
    if (bb && ba) {
        return *ba - *bb;   // * 解引用 optional，取出里面的值
    }
    return std::nullopt;
}

std::optional<double> LimitOrderBook::mid_price() const {
    auto bb = best_bid();
    auto ba = best_ask();
    if (bb && ba) {
        return (*bb + *ba) / 2.0;
    }
    return std::nullopt;
}

DepthSnapshot LimitOrderBook::get_depth(int levels) const {
    DepthSnapshot snapshot;

    // 收集买盘前 N 档
    int count = 0;
    for (const auto& [price, level] : bids_) {
        if (count >= levels) break;
        if (level.is_empty()) continue;
        snapshot.bids.push_back({price, level.total_quantity(), level.order_count()});
        count++;
    }

    // 收集卖盘前 N 档
    count = 0;
    for (const auto& [price, level] : asks_) {
        if (count >= levels) break;
        if (level.is_empty()) continue;
        snapshot.asks.push_back({price, level.total_quantity(), level.order_count()});
        count++;
    }

    // 计算价差和中间价
    auto s = spread();
    auto m = mid_price();
    snapshot.spread = s.value_or(0.0);     // value_or：如果有值就用，没有就用默认值
    snapshot.mid_price = m.value_or(0.0);

    return snapshot;
}

int LimitOrderBook::bid_quantity_at(double price) const {
    // find() 在 map 里查找 key，返回迭代器
    // 如果没找到，返回 end()
    auto it = bids_.find(price);
    if (it != bids_.end()) {
        return it->second.total_quantity();
        // it->first  = key（价格）
        // it->second = value（PriceLevel）
    }
    return 0;
}

int LimitOrderBook::ask_quantity_at(double price) const {
    auto it = asks_.find(price);
    if (it != asks_.end()) {
        return it->second.total_quantity();
    }
    return 0;
}

std::optional<BookOrder> LimitOrderBook::find_order(const std::string& order_id) const {
    // 在买盘中找
    for (const auto& [price, level] : bids_) {
        for (const auto& order : level.get_orders()) {
            if (order.order_id == order_id) {
                return order;
            }
        }
    }
    // 在卖盘中找
    for (const auto& [price, level] : asks_) {
        for (const auto& order : level.get_orders()) {
            if (order.order_id == order_id) {
                return order;
            }
        }
    }
    return std::nullopt;
}


// ============================================================
// 修改方法
// ============================================================

void LimitOrderBook::add_order(BookOrder order) {
    double price = order.price;
    Side side = order.side;

    if (side == Side::BUY) {
        // 在买盘中查找该价位
        auto it = bids_.find(price);
        if (it == bids_.end()) {
            // 该价位还不存在，创建一个新的 PriceLevel
            // emplace 是 map 的"原地构造"方法，比 insert 更高效
            // 它直接在 map 内部构造对象，不需要先构造再拷贝
            auto [new_it, _] = bids_.emplace(price, PriceLevel(price));
            // 返回值是 pair<迭代器, 是否插入成功>
            // auto [new_it, _] 用结构化绑定拆开
            // _ 是不需要的返回值（C++ 没有真正的"忽略"语法，用 _ 是惯例）
            new_it->second.add_order(std::move(order));
        } else {
            it->second.add_order(std::move(order));
        }
    } else {
        // 卖盘，同理
        auto it = asks_.find(price);
        if (it == asks_.end()) {
            auto [new_it, _] = asks_.emplace(price, PriceLevel(price));
            new_it->second.add_order(std::move(order));
        } else {
            it->second.add_order(std::move(order));
        }
    }
}

bool LimitOrderBook::cancel_order(const std::string& order_id) {
    // 先在买盘里找
    for (auto& [price, level] : bids_) {
        if (level.remove_order(order_id)) {
            return true;
        }
    }
    // 再在卖盘里找
    for (auto& [price, level] : asks_) {
        if (level.remove_order(order_id)) {
            return true;
        }
    }
    return false;
}

PriceLevel* LimitOrderBook::best_bid_level() {
    // 返回指向最高买价 PriceLevel 的指针
    // 为什么返回指针而不是引用？因为可能没有买盘，指针可以返回 nullptr
    for (auto& [price, level] : bids_) {
        if (!level.is_empty()) {
            return &level;   // & 取地址，获得指向 level 的指针
        }
    }
    return nullptr;   // 空指针，表示买盘为空
}

PriceLevel* LimitOrderBook::best_ask_level() {
    for (auto& [price, level] : asks_) {
        if (!level.is_empty()) {
            return &level;
        }
    }
    return nullptr;
}

void LimitOrderBook::cleanup() {
    // 删除空的价位，释放内存
    // 使用 erase-remove 惯用法

    // 遍历买盘，删除空价位
    // 注意：不能在遍历 map 的同时直接 erase，需要用 it++ 的技巧
    for (auto it = bids_.begin(); it != bids_.end(); ) {
        if (it->second.is_empty()) {
            it = bids_.erase(it);
            // erase 返回下一个有效的迭代器
            // 这就是为什么不写 it++ 在 for 的第三部分
        } else {
            ++it;   // 前置 ++ 比后置 ++ 效率高（对于复杂迭代器）
        }
    }

    for (auto it = asks_.begin(); it != asks_.end(); ) {
        if (it->second.is_empty()) {
            it = asks_.erase(it);
        } else {
            ++it;
        }
    }
}

}  // namespace orderbook
