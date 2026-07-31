/*
 * kdj_strategy.cpp — KDJ 金叉/死叉策略实现
 */

#include "kdj_strategy.h"

namespace backtest {

KDJStrategy::KDJStrategy(int n, int m1, int m2,
                         double oversold, double overbought,
                         double position_pct)
    : n_(n), m1_(m1), m2_(m2)
    , oversold_(oversold)
    , overbought_(overbought)
    , position_pct_(position_pct)
{
}

std::string KDJStrategy::name() const {
    return "KDJ";
}

std::string KDJStrategy::description() const {
    return "KDJ策略：K线在低位上穿D线买入，K线在高位下穿D线卖出";
}

std::map<std::string, std::string> KDJStrategy::param_schema() const {
    return {
        {"n", "KDJ周期, 默认9"},
        {"m1", "K平滑因子, 默认3"},
        {"m2", "D平滑因子, 默认3"},
        {"oversold", "超卖区域, 默认20"},
        {"overbought", "超买区域, 默认80"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

std::vector<Order> KDJStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    if (ctx.bar_index < n_) {
        return orders;
    }

    auto result = ctx.kdj(n_, m1_, m2_);

    // K 上穿 D，且在低位区域（超卖区）
    bool golden_cross = (prev_k_ <= prev_d_) && (result.k > result.d) && (result.k < overbought_);
    // K 下穿 D，且在高位区域（超买区）
    bool death_cross = (prev_k_ >= prev_d_) && (result.k < result.d) && (result.k > oversold_);

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

    prev_k_ = result.k;
    prev_d_ = result.d;

    return orders;
}

}  // namespace backtest
