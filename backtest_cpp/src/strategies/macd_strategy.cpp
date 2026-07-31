/*
 * macd_strategy.cpp — MACD 交叉策略实现
 */

#include "macd_strategy.h"

namespace backtest {

MACDStrategy::MACDStrategy(int fast_period, int slow_period,
                           int signal_period, double position_pct)
    : fast_period_(fast_period)
    , slow_period_(slow_period)
    , signal_period_(signal_period)
    , position_pct_(position_pct)
{
}

std::string MACDStrategy::name() const {
    return "MACD";
}

std::string MACDStrategy::description() const {
    return "MACD策略：DIF上穿DEA买入，DIF下穿DEA卖出";
}

std::map<std::string, std::string> MACDStrategy::param_schema() const {
    return {
        {"fast_period", "快线周期, 默认12"},
        {"slow_period", "慢线周期, 默认26"},
        {"signal_period", "信号线周期, 默认9"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

std::vector<Order> MACDStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    if (ctx.bar_index < slow_period_ + signal_period_) {
        return orders;
    }

    auto m = ctx.macd(fast_period_, slow_period_, signal_period_);

    if (!initialized_) {
        prev_dif_ = m.dif;
        prev_dea_ = m.dea;
        initialized_ = true;
        return orders;
    }

    // DIF 上穿 DEA（金叉）
    bool golden_cross = (prev_dif_ <= prev_dea_) && (m.dif > m.dea);
    // DIF 下穿 DEA（死叉）
    bool death_cross = (prev_dif_ >= prev_dea_) && (m.dif < m.dea);

    if (golden_cross && !ctx.has_position()) {
        double available = ctx.cash * position_pct_;
        // 一手股数由引擎按市场给（A股 100 / crypto 1）——⛔ 别再手写 100
        int qty = ctx.lot_floor(available / ctx.current_bar.close);
        if (qty > 0) {
            orders.push_back(Order::market_buy(ctx.symbol, qty));
        }
    }
    else if (death_cross && ctx.has_position()) {
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }

    prev_dif_ = m.dif;
    prev_dea_ = m.dea;

    return orders;
}

}  // namespace backtest
