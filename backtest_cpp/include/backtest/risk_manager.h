/*
 * risk_manager.h -- risk control module
 *
 * RiskManager enforces stop-loss and position limits at the engine level.
 * Strategies cannot bypass these rules.
 *
 * Features:
 * - Fixed stop-loss: force sell when loss exceeds a threshold
 * - Trailing stop: force sell when price drops from its highest point
 * - Position limit: cap the size of a single position
 */

#pragma once

#include "types.h"
#include <string>
#include <unordered_map>

namespace backtest {

class RiskManager {
public:
    explicit RiskManager(const RiskConfig& config = RiskConfig{});

    /*
     * on_bar -- called each bar AFTER strategy orders are executed.
     * Checks whether any risk rule is triggered and returns
     * forced-sell orders if needed.
     *
     * Parameters:
     *   symbol          : current symbol
     *   current_price   : bar close price
     *   position_qty    : current shares held
     *   avg_cost        : average entry cost
     *   total_value     : portfolio total value
     *
     * Returns: a vector of forced-sell orders (may be empty).
     */
    std::vector<Order> check_stop_loss(
        const std::string& symbol,
        double current_price,
        int position_qty,
        double avg_cost,
        double total_value);

    /*
     * filter_order -- called BEFORE executing a buy order.
     * May reduce the order quantity if it would exceed position limits.
     * Returns adjusted quantity (0 means reject).
     */
    int filter_buy_quantity(
        int requested_qty,
        double price,
        double total_value) const;

    /* Reset tracking state (call when position is fully closed). */
    void reset_tracking(const std::string& symbol);

    bool is_enabled() const { return config_.enabled; }
    const RiskConfig& config() const { return config_; }

private:
    RiskConfig config_;

    // Per-symbol tracking for trailing stop
    struct TrailingState {
        double highest_since_entry = 0.0;
    };
    std::unordered_map<std::string, TrailingState> trailing_;
};

}  // namespace backtest
