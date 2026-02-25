/*
 * momentum_strategy.cpp — 动量策略的实现
 */

#include "momentum_strategy.h"

namespace backtest {

MomentumStrategy::MomentumStrategy(int lookback,
                                    double buy_threshold,
                                    double sell_threshold,
                                    double position_pct)
    : lookback_(lookback)
    , buy_threshold_(buy_threshold)
    , sell_threshold_(sell_threshold)
    , position_pct_(position_pct)
{
}

std::string MomentumStrategy::name() const {
    return "MOMENTUM";
}

std::string MomentumStrategy::description() const {
    return "动量策略：过去N天涨幅超过阈值买入，跌幅超过阈值卖出";
}

std::map<std::string, std::string> MomentumStrategy::param_schema() const {
    return {
        {"lookback", "回看周期(天), 默认20"},
        {"buy_threshold", "买入阈值(涨幅), 默认0.05"},
        {"sell_threshold", "卖出阈值(跌幅), 默认-0.03"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

/*
 * on_bar — 每天的决策逻辑
 *
 * 使用 StrategyContext::returns(lookback) 计算过去 N 天的收益率。
 * 然后和阈值比较决定是否交易。
 */
std::vector<Order> MomentumStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    // 数据不够计算动量，跳过
    if (ctx.bar_index < lookback_) {
        return orders;
    }

    // 计算过去 lookback 天的收益率
    double momentum = ctx.returns(lookback_);

    if (momentum > buy_threshold_ && !ctx.has_position()) {
        /*
         * 涨幅超过阈值且没有持仓 → 买入
         * 表示趋势向上，追涨
         */
        double available = ctx.cash * position_pct_;
        int qty = static_cast<int>(available / ctx.current_bar.close / 100) * 100;

        if (qty >= 100) {
            orders.push_back(Order::market_buy(ctx.symbol, qty));
        }
    }
    else if (momentum < sell_threshold_ && ctx.has_position()) {
        /*
         * 跌幅超过阈值且有持仓 → 全部卖出
         * 表示趋势反转，止损出局
         */
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }

    return orders;
}

}  // namespace backtest
