/*
 * rsi_strategy.cpp — RSI 超买超卖策略实现
 */

#include "rsi_strategy.h"

namespace backtest {

RSIStrategy::RSIStrategy(int period, double oversold,
                         double overbought, double position_pct)
    : period_(period)
    , oversold_(oversold)
    , overbought_(overbought)
    , position_pct_(position_pct)
{
}

std::string RSIStrategy::name() const {
    return "RSI";
}

std::string RSIStrategy::description() const {
    return "RSI策略：RSI低于超卖区回升买入，高于超买区回落卖出";
}

std::map<std::string, std::string> RSIStrategy::param_schema() const {
    return {
        {"period", "RSI周期, 默认14"},
        {"oversold", "超卖阈值, 默认30"},
        {"overbought", "超买阈值, 默认70"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

std::vector<Order> RSIStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    if (ctx.bar_index < period_ + 1) {
        return orders;
    }

    double current_rsi = ctx.rsi(period_);

    // RSI 从超卖区回升（上穿 oversold 线）
    bool oversold_bounce = (prev_rsi_ < oversold_) && (current_rsi >= oversold_);
    // RSI 从超买区回落（下穿 overbought 线）
    bool overbought_drop = (prev_rsi_ > overbought_) && (current_rsi <= overbought_);

    if (oversold_bounce && !ctx.has_position()) {
        double available = ctx.cash * position_pct_;
        int qty = static_cast<int>(available / ctx.current_bar.close / 100) * 100;
        if (qty >= 100) {
            orders.push_back(Order::market_buy(ctx.symbol, qty));
        }
    }
    else if (overbought_drop && ctx.has_position()) {
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }

    prev_rsi_ = current_rsi;

    return orders;
}

}  // namespace backtest
