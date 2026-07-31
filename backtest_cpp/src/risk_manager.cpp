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
    double total_value,
    double symbol_position_value,
    double total_position_value) const
{
    if (price <= 0.0) {
        return requested_qty;
    }

    /*
     * ⚠️ **仓位上限不看 `enabled`**（2026-07-31 改）。
     *
     * `enabled` 管的是止损/追踪止损那套「盯着行情动手」的风控。仓位上限是
     * **账户结构约束**，性质完全不同。旧实现把两者绑在一起，后果是：
     * `enabled=false`（**服务端默认值**）时 `max_total_position_pct=0.8`
     * 一行代码都不走 —— 20% 现金保护配了等于没配，而且**看不出来**。
     *
     * 现在的口径：**哪条上限被配成 <1.0，哪条就生效**，与 enabled 无关。
     * 两条都是 1.0（默认）时下面两个 if 都不进，行为与从前一致。
     */
    int allowed = requested_qty;

    // ── ① 单标的上限（含已有持仓）──
    if (config_.max_position_pct < 1.0) {
        double headroom = total_value * config_.max_position_pct - symbol_position_value;
        allowed = std::min(allowed, _qty_from_headroom(headroom, price));
    }

    // ── ② 总仓位上限（留 1-Y 现金）──
    // 🔒 与 ① 是两条独立约束：这里是 min 不是 max，两条都要过。
    if (config_.max_total_position_pct < 1.0) {
        double headroom = total_value * config_.max_total_position_pct - total_position_value;
        allowed = std::min(allowed, _qty_from_headroom(headroom, price));
    }

    return std::max(0, allowed);
}

/*
 * headroom / price → 股数。
 * ⚠️ 必须先在 double 域里钳上界再转 int：极便宜的标的（价格 1e-5）算出来是 8e9，
 * 直接 static_cast<int> 在 x86 上是 UB → 变成 INT_MIN → 被 max(0,…) 吃成
 * **静默零成交**。crypto 组合回测不做价格缩放时真的会走到这个量级。
 */
int RiskManager::_qty_from_headroom(double headroom, double price) {
    if (!(headroom > 0.0) || !(price > 0.0)) return 0;
    double qty = headroom / price;
    if (qty > 2000000000.0) return 2000000000;
    return static_cast<int>(qty);
}

void RiskManager::reset_tracking(const std::string& symbol) {
    trailing_.erase(symbol);
}

}  // namespace backtest
