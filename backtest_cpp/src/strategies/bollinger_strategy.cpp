/*
 * bollinger_strategy.cpp — 布林带策略实现
 */

#include "bollinger_strategy.h"

namespace backtest {

BollingerStrategy::BollingerStrategy(int period, double num_std, double position_pct)
    : period_(period)
    , num_std_(num_std)
    , position_pct_(position_pct)
{
}

std::string BollingerStrategy::name() const {
    return "BOLLINGER";
}

std::string BollingerStrategy::description() const {
    return "布林带策略：价格触及下轨反弹买入，触及上轨回落卖出";
}

std::map<std::string, std::string> BollingerStrategy::param_schema() const {
    return {
        {"period", "布林带周期, 默认20"},
        {"num_std", "标准差倍数, 默认2.0"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

std::vector<Order> BollingerStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    if (ctx.bar_index < period_) {
        return orders;
    }

    auto bb = ctx.bollinger(period_, num_std_);
    double close = ctx.current_bar.close;

    // 价格从下轨下方反弹回来（上穿下轨）
    bool bounce_from_lower = (prev_close_ <= prev_lower_ && prev_lower_ > 0) && (close > bb.lower);
    // 价格从上轨上方回落（下穿上轨）
    bool drop_from_upper = (prev_close_ >= prev_upper_ && prev_upper_ > 0) && (close < bb.upper);

    if (bounce_from_lower && !ctx.has_position()) {
        double available = ctx.cash * position_pct_;
        int qty = static_cast<int>(available / ctx.current_bar.close / 100) * 100;
        if (qty >= 100) {
            orders.push_back(Order::market_buy(ctx.symbol, qty));
        }
    }
    else if (drop_from_upper && ctx.has_position()) {
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }

    prev_close_ = close;
    prev_lower_ = bb.lower;
    prev_upper_ = bb.upper;

    return orders;
}

}  // namespace backtest
