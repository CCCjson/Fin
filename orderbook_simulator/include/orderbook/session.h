/*
 * ============================================================
 * session.h — 订单簿会话
 * ============================================================
 *
 * 一个 Session 代表一次"实验"或"模拟"：
 *   - 有自己独立的订单簿 (LimitOrderBook)
 *   - 有自己的撮合引擎 (MatchingEngine)
 *   - 记录所有成交 (fills)
 *   - 可以随机播种订单来模拟真实盘口
 *
 * 每个 Session 彼此独立，互不影响。
 * 就像开了多个"平行世界"的交易所。
 *
 * ============================================================
 */

#ifndef ORDERBOOK_SESSION_H
#define ORDERBOOK_SESSION_H

#include <string>
#include <vector>
#include "orderbook/types.h"
#include "orderbook/limit_order_book.h"
#include "orderbook/matching_engine.h"
#include "orderbook/statistics.h"
#include "orderbook/market_impact.h"

namespace orderbook {

class Session {
public:
    /// 创建一个新的会话
    ///
    /// 参数：
    ///   session_id — 会话唯一标识
    ///   symbol     — 模拟的股票代码（如 "AAPL"）
    Session(const std::string& session_id, const std::string& symbol);

    // ── 查询 ──

    const std::string& session_id() const;
    const std::string& symbol() const;

    /// 获取盘口深度
    DepthSnapshot get_depth(int levels = 10) const;

    /// 获取所有成交记录
    const std::vector<Fill>& get_fills() const;

    /// 获取最近 N 笔成交
    std::vector<Fill> get_recent_fills(int limit = 50) const;

    /// 获取盘口统计
    BookStats get_stats(int depth_levels = 10) const;

    /// 估算市场冲击
    double estimate_impact(int quantity, double volatility, double daily_volume) const;

    /// 查找订单
    std::optional<BookOrder> find_order(const std::string& order_id) const;

    // ── 操作 ──

    /// 提交一个订单
    MatchResult submit_order(BookOrder order);

    /// 撤销一个订单
    bool cancel_order(const std::string& order_id);

    /// 随机播种限价单，模拟一个有流动性的盘口
    ///
    /// 参数：
    ///   count      — 生成多少个订单
    ///   mid_price  — 中间价（买卖盘围绕这个价格生成）
    ///   tick_size  — 最小价格变动单位（如 0.01）
    ///   spread_ticks — 价差有多少个 tick（如 5 表示买卖价差 = 5 × tick_size）
    ///   depth_ticks  — 盘口深度有多少个 tick（向两侧延伸多远）
    ///   min_qty    — 最小订单量
    ///   max_qty    — 最大订单量
    ///
    /// 返回：实际生成了多少个订单
    int seed_orders(
        int count,
        double mid_price,
        double tick_size = 0.01,
        int spread_ticks = 2,
        int depth_ticks = 20,
        int min_qty = 100,
        int max_qty = 1000
    );

private:
    std::string session_id_;
    std::string symbol_;
    LimitOrderBook book_;          // 该会话的订单簿
    MatchingEngine engine_;        // 该会话的撮合引擎
    std::vector<Fill> all_fills_;  // 所有成交记录
};

}  // namespace orderbook

#endif // ORDERBOOK_SESSION_H
