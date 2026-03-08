/*
 * pairs_strategy.cpp — 统计套利策略实现
 */

#include "pairs_strategy.h"
#include <numeric>

namespace backtest {

PairsStrategy::PairsStrategy(const std::string& symbol2,
                             const std::vector<Bar>& bars2,
                             int lookback, double entry_z,
                             double exit_z, double position_pct)
    : symbol2_(symbol2)
    , bars2_(bars2)
    , lookback_(lookback)
    , entry_z_(entry_z)
    , exit_z_(exit_z)
    , position_pct_(position_pct)
{
}

std::string PairsStrategy::name() const {
    return "PAIRS";
}

std::string PairsStrategy::description() const {
    return "统计套利策略：基于两只股票价差的z-score进行均值回归交易";
}

std::map<std::string, std::string> PairsStrategy::param_schema() const {
    return {
        {"symbol2", "配对股票代码"},
        {"lookback", "回看周期, 默认60"},
        {"entry_z", "入场z-score阈值, 默认2.0"},
        {"exit_z", "出场z-score阈值, 默认0.5"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

std::vector<Order> PairsStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    int idx = ctx.bar_index;

    // 数据不够或第二只股票数据不够
    if (idx < lookback_ || idx >= static_cast<int>(bars2_.size())) {
        return orders;
    }

    // 计算价差序列的 z-score
    // 价差 = log(priceA) - log(priceB)
    std::vector<double> spreads;
    spreads.reserve(lookback_);

    int hist_size = static_cast<int>(ctx.history->size());
    for (int i = hist_size - lookback_; i < hist_size; ++i) {
        double pa = (*ctx.history)[i].close;
        // bars2_ 的索引需要对齐：bars2_ 和 history 应该是同样的日期范围
        // 用 bar_index 来推算 bars2_ 中对应的索引
        int b2_idx = idx - (hist_size - 1 - i);
        if (b2_idx < 0 || b2_idx >= static_cast<int>(bars2_.size())) continue;
        double pb = bars2_[b2_idx].close;
        if (pa > 0 && pb > 0) {
            spreads.push_back(std::log(pa) - std::log(pb));
        }
    }

    if (static_cast<int>(spreads.size()) < lookback_ / 2) {
        return orders; // 有效数据太少
    }

    // 计算均值和标准差
    double mean = std::accumulate(spreads.begin(), spreads.end(), 0.0) / spreads.size();
    double sq_sum = 0.0;
    for (double s : spreads) {
        sq_sum += (s - mean) * (s - mean);
    }
    double stddev = std::sqrt(sq_sum / spreads.size());

    if (stddev < 1e-10) return orders; // 标准差太小

    double current_spread = spreads.back();
    double z_score = (current_spread - mean) / stddev;

    if (z_score < -entry_z_ && !ctx.has_position()) {
        // z < -entry_z → 价差低于均值，买入 A
        double available = ctx.cash * position_pct_;
        int qty = static_cast<int>(available / ctx.current_bar.close / 100) * 100;
        if (qty >= 100) {
            orders.push_back(Order::market_buy(ctx.symbol, qty));
        }
    }
    else if (z_score > entry_z_ && !ctx.has_position()) {
        // z > entry_z → 价差高于均值，卖出 A（在只能做多的情况下不操作）
        // 如果有持仓则平仓
    }
    else if (std::abs(z_score) < exit_z_ && ctx.has_position()) {
        // |z| < exit_z → 价差回归均值，平仓
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }
    else if (z_score > entry_z_ && ctx.has_position()) {
        // z 反向超阈值，止损平仓
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }

    return orders;
}

}  // namespace backtest
