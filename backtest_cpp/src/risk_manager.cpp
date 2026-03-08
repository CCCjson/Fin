/*
 * risk_manager.cpp -- Risk control implementation
 */

#include "backtest/risk_manager.h"
#include <cmath>
#include <algorithm>

namespace backtest {

RiskManager::RiskManager(const RiskConfig& config)
    : config_(config)
{
}

std::vector<Order> RiskManager::check_stop_loss(
    const std::string& symbol,
    double current_price,
    int position_qty,
    double avg_cost,
    double total_value)
{
    std::vector<Order> orders;
    if (!config_.enabled || position_qty <= 0 || avg_cost <= 0.0) {
        return orders;
    }

    double loss_pct = (avg_cost - current_price) / avg_cost;
    bool triggered = false;
    std::string reason;

    // 1. Fixed stop-loss
    if (loss_pct >= config_.stop_loss_pct) {
        triggered = true;
        reason = "stop_loss";
    }

    // 2. Trailing stop
    if (!triggered && config_.trailing_stop) {
        auto& state = trailing_[symbol];

        // Update highest price since entry
        if (state.highest_since_entry <= 0.0) {
            state.highest_since_entry = avg_cost;
        }
        state.highest_since_entry = std::max(state.highest_since_entry, current_price);

        double drop_pct = (state.highest_since_entry - current_price) / state.highest_since_entry;
        if (drop_pct >= config_.trailing_stop_pct) {
            triggered = true;
            reason = "trailing_stop";
        }
    }

    if (triggered) {
        auto order = Order::market_sell(symbol, position_qty);
        order.order_id = "RISK_" + reason;
        orders.push_back(order);
    }

    return orders;
}

int RiskManager::filter_buy_quantity(
    int requested_qty,
    double price,
    double total_value) const
{
    if (!config_.enabled || config_.max_position_pct >= 1.0) {
        return requested_qty;  // no limit
    }

    double max_amount = total_value * config_.max_position_pct;
    int max_qty = static_cast<int>(max_amount / price);
    return std::min(requested_qty, max_qty);
}

void RiskManager::reset_tracking(const std::string& symbol) {
    trailing_.erase(symbol);
}

}  // namespace backtest
