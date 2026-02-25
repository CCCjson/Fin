/*
 * ============================================================
 * limit_order_book.h — 限价订单簿（核心数据结构）
 * ============================================================
 *
 * 限价订单簿（Limit Order Book, LOB）是证券交易所的核心。
 * 它维护两棵"价格树"：
 *
 *   卖盘 (asks) — 从低到高排列：
 *     102.00  [200 股]
 *     101.50  [300 股]
 *     101.00  [150 股]  ← best_ask（最低卖价，最优卖价）
 *     ─────── 价差 (spread) ───────
 *     100.50  [100 股]  ← best_bid（最高买价，最优买价）
 *     100.00  [250 股]
 *      99.50  [400 股]
 *   买盘 (bids) — 从高到低排列
 *
 * 为什么买盘要从高到低？
 *   因为出价最高的买家应该排在最前面（最有可能成交）
 *
 * 为什么卖盘要从低到高？
 *   因为要价最低的卖家应该排在最前面（最有可能成交）
 *
 * 数据结构选择：std::map
 *   std::map 内部是红黑树（一种自平衡二叉搜索树），key 自动排序。
 *   - 插入/删除/查找都是 O(log n)
 *   - key 始终有序，可以快速取到最大/最小值
 *   - 比 unordered_map（哈希表）多了排序功能，这正是我们需要的
 *
 * ============================================================
 */

#ifndef ORDERBOOK_LIMIT_ORDER_BOOK_H
#define ORDERBOOK_LIMIT_ORDER_BOOK_H

#include <map>
#include <optional>    // std::optional — 表示"可能没有值"
#include <vector>
#include <string>
#include <functional>  // std::greater — 用于让 map 降序排列
#include "orderbook/types.h"
#include "orderbook/price_level.h"

namespace orderbook {

/// 一档盘口信息（用于 get_depth 返回值）
struct DepthLevel {
    double price;
    int quantity;       // 该价位总量
    int order_count;    // 该价位订单数

    nlohmann::json to_json() const {
        return {
            {"price", price},
            {"quantity", quantity},
            {"order_count", order_count}
        };
    }
};

/// 盘口快照（买卖双方的深度信息）
struct DepthSnapshot {
    std::vector<DepthLevel> bids;    // 买盘（从高到低）
    std::vector<DepthLevel> asks;    // 卖盘（从低到高）
    double spread;                    // 买卖价差
    double mid_price;                 // 中间价

    nlohmann::json to_json() const {
        nlohmann::json j;
        j["bids"] = nlohmann::json::array();
        for (const auto& b : bids) j["bids"].push_back(b.to_json());
        j["asks"] = nlohmann::json::array();
        for (const auto& a : asks) j["asks"].push_back(a.to_json());
        j["spread"] = spread;
        j["mid_price"] = mid_price;
        return j;
    }
};


class LimitOrderBook {
public:
    LimitOrderBook();

    // ============================================================
    // 查询方法（只读）
    // ============================================================

    /// 最优买价（买盘里出价最高的）
    /// 返回 std::optional<double>：
    ///   如果买盘有订单 → 返回最高买价
    ///   如果买盘为空   → 返回 std::nullopt（表示"没有值"）
    /// 为什么用 optional？因为订单簿可能是空的，没有最优价
    std::optional<double> best_bid() const;

    /// 最优卖价（卖盘里要价最低的）
    std::optional<double> best_ask() const;

    /// 买卖价差 = best_ask - best_bid
    /// 如果任何一边为空，返回 nullopt
    std::optional<double> spread() const;

    /// 中间价 = (best_bid + best_ask) / 2
    std::optional<double> mid_price() const;

    /// 获取盘口深度（前 N 档）
    DepthSnapshot get_depth(int levels = 10) const;

    /// 获取买盘某价位上的总量（如果该价位不存在则返回 0）
    int bid_quantity_at(double price) const;

    /// 获取卖盘某价位上的总量
    int ask_quantity_at(double price) const;

    /// 通过 order_id 查找订单（可能在买盘或卖盘里）
    std::optional<BookOrder> find_order(const std::string& order_id) const;

    // ============================================================
    // 修改方法
    // ============================================================

    /// 添加一个限价单到订单簿
    /// 买单放到 bids_，卖单放到 asks_
    void add_order(BookOrder order);

    /// 撤销一个订单（需要同时知道是在买盘还是卖盘）
    /// 返回是否成功
    bool cancel_order(const std::string& order_id);

    /// 获取买盘对手盘（给卖单撮合用）— 从最高买价开始
    /// 返回指向内部 PriceLevel 的指针，调用者通过它撮合
    PriceLevel* best_bid_level();

    /// 获取卖盘对手盘（给买单撮合用）— 从最低卖价开始
    PriceLevel* best_ask_level();

    /// 移除空的价位（撮合后某价位可能没有订单了）
    void cleanup();

private:
    // ── 核心数据 ──

    // 买盘：std::map<价格, PriceLevel, 比较器>
    //
    // std::greater<double> 让 map 按 key 从大到小排列：
    //   100.50 → PriceLevel(100.50)   ← begin() 指向这里（最高买价）
    //   100.00 → PriceLevel(100.00)
    //    99.50 → PriceLevel(99.50)
    //
    // 默认的 map 是从小到大排的，但买盘需要最高价在前面
    std::map<double, PriceLevel, std::greater<double>> bids_;

    // 卖盘：默认升序就行（最低卖价在前面）
    //   101.00 → PriceLevel(101.00)   ← begin() 指向这里（最低卖价）
    //   101.50 → PriceLevel(101.50)
    //   102.00 → PriceLevel(102.00)
    std::map<double, PriceLevel> asks_;
};

}  // namespace orderbook

#endif // ORDERBOOK_LIMIT_ORDER_BOOK_H
