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
     * filter_buy_quantity -- 买单成交前的仓位闸门，返回放行的数量（0 = 拒绝）。
     *
     * 🔒 两条**互相独立**的上限，必须**同时**满足，所以取两者算出的更小值：
     *   ① max_position_pct        —— 本标的持仓（含这笔）不得超过总资产的 X%
     *   ② max_total_position_pct  —— 全部持仓合计不得超过总资产的 Y%（留 1-Y 现金）
     *
     * ⛔ **绝对不许写成 max(①, ②)**。那会静默撤销现金保护：单标的上限 0.95
     *    会把总仓位上限 0.8 顶掉，20% 现金一分不剩。这个 bug 在 Python 侧
     *    被复制过三份，教训见 `risk-total-position-floor`。有门禁测试盯着。
     *
     * ⚠️ ① 必须把**已有持仓**算进去。旧版只看这一笔订单的名义额，于是
     *    「今天买 20%、明天再买 20%」能堆到 40% 而闸门全程放行。
     *
     * ⚠️ **两条上限都不看 `enabled`**：`enabled` 管的是止损那套，仓位上限是
     *    账户结构约束。绑在一起的后果是 `enabled=false`（服务端默认）时
     *    20% 现金保护配了等于没配，且看不出来。
     *
     * ⚠️ 这是**下单闸门**，不是**再平衡器**：它只管「不让你买到上限以上」。
     *    买完之后行情涨上去、持仓占比漂过上限，**不会**触发强制卖出 ——
     *    那样会在牛市里不停砍掉赢利仓位白交手续费，而且等于规则替人做减仓决策。
     *    ⛔ 看到「持仓 85% > 80%」先别断定闸门漏了，先看是不是涨出来的。
     *
     * 🔴 调用方在**同一天连发多个买单**时，必须把已放行的名义额**累加进**
     *    `total_position_value` 再调下一次** —— 本函数无状态，不会替你扣额度。
     *    否则 N 个买单各自都在 80% 以内、合计却穿到 100%。（踩过）
     *
     * 参数：
     *   symbol_position_value : 本标的**现有**持仓市值（含同日已放行的部分）
     *   total_position_value  : 全部持仓**现有**市值合计（同上）
     */
    int filter_buy_quantity(
        int requested_qty,
        double price,
        double total_value,
        double symbol_position_value = 0.0,
        double total_position_value = 0.0) const;

    /*
     * 只过 ①（单标的上限）。
     *
     * ⭐ 拆出来是因为**两条上限的分配性质完全不同**：
     *   ① 单标的上限是每个标的自己的事，逐单独立判，顺序无关；
     *   ② 总仓位上限是**一份公共额度**，同一天多个买单必须**等比分配** ——
     *      逐单先到先得的话，谁买得到由 std::map 的字母序决定
     *      （BTC 永远赢、SOL 永远饿死），而且现金没花完时资金竞争那条路
     *      根本不触发 → 全程静默。这个坑踩过，见 engine.cpp 的 (2b) 段。
     */
    int filter_symbol_quantity(
        int requested_qty,
        double price,
        double total_value,
        double symbol_position_value = 0.0) const;

    /* Reset tracking state (call when position is fully closed). */
    void reset_tracking(const std::string& symbol);

    bool is_enabled() const { return config_.enabled; }

    /* 有没有配任何仓位上限（与 enabled 无关，见 filter_buy_quantity 的说明）。 */
    bool has_position_caps() const {
        return config_.max_position_pct < 1.0 || config_.max_total_position_pct < 1.0;
    }

    /* 有没有配总仓位上限（= 有没有现金底线要守）。 */
    bool has_total_cap() const { return config_.max_total_position_pct < 1.0; }

    const RiskConfig& config() const { return config_; }

private:
    /* headroom/price → 股数（先在 double 域钳上界，避免极低价标的转 int 溢出）*/
    static int _qty_from_headroom(double headroom, double price);

    RiskConfig config_;

    // Per-symbol tracking for trailing stop
    struct TrailingState {
        double highest_since_entry = 0.0;
    };
    std::unordered_map<std::string, TrailingState> trailing_;
};

}  // namespace backtest
