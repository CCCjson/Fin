/*
 * statistics.cpp — 盘口统计的实现
 */

#include "orderbook/statistics.h"

namespace orderbook {

BookStats Statistics::calculate(
    const LimitOrderBook& book,
    const std::vector<Fill>& fills,
    int depth_levels
) {
    BookStats stats;

    // ── 盘口信息 ──
    auto depth = book.get_depth(depth_levels);

    stats.spread = depth.spread;
    stats.mid_price = depth.mid_price;

    // 相对价差（基点）
    // 基点 (basis point, bps) = 万分之一 = 0.01%
    // 比如价差 0.50 / 中间价 100.25 = 0.00499 = 49.9 bps
    if (stats.mid_price > 0) {
        stats.spread_bps = (stats.spread / stats.mid_price) * 10000.0;
    } else {
        stats.spread_bps = 0.0;
    }

    // ── 买卖盘深度 ──
    stats.bid_depth = 0;
    for (const auto& level : depth.bids) {
        stats.bid_depth += level.quantity;
    }

    stats.ask_depth = 0;
    for (const auto& level : depth.asks) {
        stats.ask_depth += level.quantity;
    }

    // ── 买卖失衡 ──
    // imbalance = (买量 - 卖量) / (买量 + 卖量)
    // 范围 [-1, 1]：
    //   +1 = 全是买盘（看涨信号）
    //    0 = 买卖均衡
    //   -1 = 全是卖盘（看跌信号）
    int total_depth = stats.bid_depth + stats.ask_depth;
    if (total_depth > 0) {
        stats.imbalance = static_cast<double>(stats.bid_depth - stats.ask_depth)
                          / static_cast<double>(total_depth);
    } else {
        stats.imbalance = 0.0;
    }

    // ── VWAP（成交量加权平均价）──
    // VWAP = Σ(每笔成交价 × 成交量) / Σ(成交量)
    // 它反映了"大家平均在什么价格上成交"
    double price_volume_sum = 0.0;   // 分子：价格 × 数量 的累加
    int volume_sum = 0;              // 分母：数量累加

    for (const auto& fill : fills) {
        price_volume_sum += fill.price * fill.quantity;
        volume_sum += fill.quantity;
    }

    stats.vwap = (volume_sum > 0)
                 ? price_volume_sum / static_cast<double>(volume_sum)
                 : 0.0;

    // ── 成交统计 ──
    stats.total_fills = static_cast<int>(fills.size());
    // fills.size() 返回 size_t（无符号整数），转成 int 更方便使用
    stats.total_volume = volume_sum;

    return stats;
}

}  // namespace orderbook
