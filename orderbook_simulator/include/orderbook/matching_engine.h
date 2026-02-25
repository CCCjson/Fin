/*
 * ============================================================
 * matching_engine.h — 撮合引擎
 * ============================================================
 *
 * 撮合引擎是订单簿的"大脑"。当一个新订单进来时，
 * 撮合引擎决定它能不能成交、和谁成交、成交多少。
 *
 * 核心规则：价格优先、时间优先
 *   1. 价格优先：出价高的买单先成交，要价低的卖单先成交
 *   2. 时间优先：同一价格上，先来的订单先成交（FIFO）
 *
 * 四种订单类型的处理逻辑：
 *
 *   MARKET（市价单）：
 *     "我不在乎价格，现在就要买/卖"
 *     → 无条件从对手盘最优价开始吃，一直吃到数量满足或对手盘吃完
 *
 *   LIMIT（限价单）：
 *     "我最多出 101.00 买 / 最少要 99.00 卖"
 *     → 先看对手盘有没有可以成交的价格，能成交的先成交
 *     → 剩余的挂到订单簿上等待
 *
 *   IOC（Immediate or Cancel）：
 *     "能成多少成多少，剩下的不要了"
 *     → 和 LIMIT 类似地撮合，但剩余部分直接取消（不挂单）
 *
 *   FOK（Fill or Kill）：
 *     "要么全部成交，要么一股都不要"
 *     → 先检查对手盘总量够不够，够就全部成交，不够就整单拒绝
 *
 * ============================================================
 */

#ifndef ORDERBOOK_MATCHING_ENGINE_H
#define ORDERBOOK_MATCHING_ENGINE_H

#include <vector>
#include "orderbook/types.h"
#include "orderbook/limit_order_book.h"

namespace orderbook {

/// 撮合结果
struct MatchResult {
    std::string order_id;               // 订单 ID
    std::vector<Fill> fills;            // 成交记录列表
    int filled_quantity;                // 总成交量
    int remaining_quantity;             // 剩余未成交量
    bool is_resting;                    // 是否有剩余挂在订单簿上
    bool is_rejected;                   // 是否被拒绝（FOK 且量不够）

    nlohmann::json to_json() const {
        nlohmann::json j;
        j["order_id"] = order_id;
        j["filled_quantity"] = filled_quantity;
        j["remaining_quantity"] = remaining_quantity;
        j["is_resting"] = is_resting;
        j["is_rejected"] = is_rejected;
        j["fills"] = nlohmann::json::array();
        for (const auto& f : fills) {
            j["fills"].push_back(f.to_json());
        }
        return j;
    }
};


class MatchingEngine {
public:
    MatchingEngine();

    /// 提交一个订单到订单簿进行撮合
    ///
    /// 参数：
    ///   book  — 订单簿的引用（会被修改：成交后对手盘减少，剩余挂单增加）
    ///   order — 新进来的订单
    ///
    /// 返回：
    ///   MatchResult — 包含成交记录、成交量等信息
    MatchResult submit_order(LimitOrderBook& book, BookOrder order);

private:
    /// 处理市价单
    MatchResult handle_market_order(LimitOrderBook& book, BookOrder& order);

    /// 处理限价单
    MatchResult handle_limit_order(LimitOrderBook& book, BookOrder& order);

    /// 处理 IOC 单
    MatchResult handle_ioc_order(LimitOrderBook& book, BookOrder& order);

    /// 处理 FOK 单
    MatchResult handle_fok_order(LimitOrderBook& book, BookOrder& order);

    /// 通用的撮合逻辑：买单吃卖盘 / 卖单吃买盘
    /// 参数 price_limit：限价单的价格上限/下限（市价单传 0 表示不限）
    std::vector<Fill> match_against_book(
        LimitOrderBook& book,
        BookOrder& order,
        double price_limit
    );

    /// 计算对手盘在指定价格范围内的总可用数量（FOK 检查用）
    int available_quantity(
        const LimitOrderBook& book,
        Side aggressor_side,
        double price_limit
    ) const;
};

}  // namespace orderbook

#endif // ORDERBOOK_MATCHING_ENGINE_H
