/*
 * ============================================================
 * market_impact.h — 市场冲击模型
 * ============================================================
 *
 * 什么是"市场冲击"？
 *
 * 假设你想买 10000 股，但卖盘上只有：
 *   101.00 挂了 2000 股
 *   101.50 挂了 3000 股
 *   102.00 挂了 5000 股
 *
 * 你的大单会把这三个价位全吃掉，平均成交价大约 101.55。
 * 但如果你只买 100 股，成交价就是 101.00。
 * 这个价差（101.55 - 101.00 = 0.55）就是"市场冲击"——
 * 你的交易本身推动了价格。
 *
 * 平方根冲击模型（Square Root Impact Model）：
 *   impact = σ × √(Q / V)
 *
 *   σ = 标的的日波动率（比如 0.02 表示每天波动 2%）
 *   Q = 你想交易的数量
 *   V = 该标的的日均成交量
 *
 * 这是业界最常用的简化模型，来自 Almgren & Chriss (2000)。
 *
 * ============================================================
 */

#ifndef ORDERBOOK_MARKET_IMPACT_H
#define ORDERBOOK_MARKET_IMPACT_H

#include <cmath>   // std::sqrt — 平方根函数

namespace orderbook {

class MarketImpact {
public:
    /// 计算市场冲击（价格影响百分比）
    ///
    /// 参数：
    ///   volatility      — 日波动率（如 0.02 = 2%）
    ///   trade_quantity   — 交易数量
    ///   daily_volume     — 日均成交量
    ///
    /// 返回：
    ///   冲击百分比（如 0.005 = 0.5%）
    ///
    /// 如果 daily_volume 为 0，返回 0（避免除以零）
    static double estimate_impact(
        double volatility,
        int trade_quantity,
        double daily_volume
    ) {
        // static 方法不需要创建对象就能调用：
        //   double impact = MarketImpact::estimate_impact(0.02, 1000, 100000);
        // 类似于 Python 的 @staticmethod

        if (daily_volume <= 0) return 0.0;

        // Q/V = 交易量占日均成交量的比例
        // 比例越大，冲击越大
        double participation_rate = static_cast<double>(trade_quantity) / daily_volume;
        // static_cast<double> 是类型转换
        // 把 int 转成 double，避免整数除法（整数除法会丢掉小数部分）

        // 平方根冲击公式
        return volatility * std::sqrt(participation_rate);
    }

    /// 计算冲击的金额（绝对值）
    ///
    /// 参数多一个 current_price — 当前价格
    /// 返回：冲击导致的价格偏移量（元）
    static double estimate_impact_price(
        double current_price,
        double volatility,
        int trade_quantity,
        double daily_volume
    ) {
        double impact_pct = estimate_impact(volatility, trade_quantity, daily_volume);
        return current_price * impact_pct;
    }
};

}  // namespace orderbook

#endif // ORDERBOOK_MARKET_IMPACT_H
