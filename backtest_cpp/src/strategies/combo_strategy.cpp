/*
 * combo_strategy.cpp — 组合策略实现
 */

#include "combo_strategy.h"

namespace backtest {

ComboStrategy::ComboStrategy(double threshold, double position_pct)
    : threshold_(threshold)
    , position_pct_(position_pct)
{
}

void ComboStrategy::add_sub_strategy(const std::string& name, double weight,
                                     std::unique_ptr<IStrategy> strategy) {
    SubStrategyConfig cfg;
    cfg.name = name;
    cfg.weight = weight;
    cfg.strategy = std::move(strategy);
    sub_strategies_.push_back(std::move(cfg));
}

std::string ComboStrategy::name() const {
    return "COMBO";
}

std::string ComboStrategy::description() const {
    return "组合策略：多个子策略加权融合信号，超过阈值触发交易";
}

std::map<std::string, std::string> ComboStrategy::param_schema() const {
    return {
        {"threshold", "信号阈值, 默认0.5"},
        {"position_pct", "仓位比例, 默认0.95"},
        {"sub_strategies", "子策略配置数组 [{name, weight, params}]"}
    };
}

std::vector<Order> ComboStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    if (sub_strategies_.empty()) {
        return orders;
    }

    double weighted_signal = 0.0;
    double total_weight = 0.0;

    // 分别用"无仓位"和"有仓位"的 ctx 调用子策略，
    // 这样子策略的 BUY 和 SELL 分支都能被触发
    StrategyContext buy_ctx = ctx;
    buy_ctx.position_quantity = 0;
    buy_ctx.position_avg_price = 0.0;

    StrategyContext sell_ctx = ctx;
    sell_ctx.position_quantity = 1000;   // 模拟持仓
    sell_ctx.position_avg_price = ctx.current_bar.close;

    for (auto& sub : sub_strategies_) {
        // 检测买入信号
        auto buy_orders = sub.strategy->on_bar(buy_ctx);
        // 检测卖出信号
        auto sell_orders = sub.strategy->on_bar(sell_ctx);

        // 从子策略返回的订单推断信号方向
        double signal = 0.0;
        for (const auto& order : buy_orders) {
            if (order.side == Side::BUY) {
                signal = 1.0;
                break;
            }
        }
        if (signal == 0.0) {
            for (const auto& order : sell_orders) {
                if (order.side == Side::SELL) {
                    signal = -1.0;
                    break;
                }
            }
        }

        weighted_signal += signal * sub.weight;
        total_weight += sub.weight;
    }

    if (total_weight == 0.0) return orders;

    double normalized_signal = weighted_signal / total_weight;

    if (normalized_signal > threshold_ && !ctx.has_position()) {
        double available = ctx.cash * position_pct_;
        int qty = static_cast<int>(available / ctx.current_bar.close / 100) * 100;
        if (qty >= 100) {
            orders.push_back(Order::market_buy(ctx.symbol, qty));
        }
    }
    else if (normalized_signal < -threshold_ && ctx.has_position()) {
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }

    return orders;
}

}  // namespace backtest
