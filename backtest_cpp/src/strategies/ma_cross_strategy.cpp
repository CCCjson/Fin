/*
 * ma_cross_strategy.cpp — 均线交叉策略的实现
 */

#include "ma_cross_strategy.h"
#include <cmath>       // std::floor

namespace backtest {

MACrossStrategy::MACrossStrategy(int fast_period, int slow_period, double position_pct)
    : fast_period_(fast_period)
    , slow_period_(slow_period)
    , position_pct_(position_pct)
{
}

std::string MACrossStrategy::name() const {
    return "MA_CROSS";
}

std::string MACrossStrategy::description() const {
    return "均线交叉策略：短期均线上穿长期均线买入，下穿卖出";
}

std::map<std::string, std::string> MACrossStrategy::param_schema() const {
    return {
        {"fast_period", "快线周期(天), 默认5"},
        {"slow_period", "慢线周期(天), 默认20"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

/*
 * on_bar — 每天的决策逻辑
 *
 * 流程：
 * 1. 计算当前的快线 SMA 和慢线 SMA
 * 2. 比较当前值和上一根 bar 的值，判断是否发生"穿越"
 * 3. 金叉 → 买入，死叉 → 卖出
 *
 * "穿越"判断：
 * 金叉 = 上一根 bar 快线 ≤ 慢线，且当前 bar 快线 > 慢线
 * 死叉 = 上一根 bar 快线 ≥ 慢线，且当前 bar 快线 < 慢线
 */
std::vector<Order> MACrossStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    // 数据不够计算慢线，跳过
    if (ctx.bar_index < slow_period_) {
        return orders;
    }

    // 使用 StrategyContext 提供的 sma() 方法计算均线
    double fast_ma = ctx.sma(fast_period_);
    double slow_ma = ctx.sma(slow_period_);

    // 第一次计算，只记录不交易（因为没有"上一根"的值）
    if (prev_fast_ma_ == 0.0 && prev_slow_ma_ == 0.0) {
        prev_fast_ma_ = fast_ma;
        prev_slow_ma_ = slow_ma;
        return orders;
    }

    /*
     * 金叉判断：
     * 之前快线在慢线下方（prev_fast ≤ prev_slow）
     * 现在快线在慢线上方（fast > slow）
     * → 快线从下往上穿越慢线
     */
    bool golden_cross = (prev_fast_ma_ <= prev_slow_ma_) && (fast_ma > slow_ma);

    /*
     * 死叉判断：
     * 之前快线在慢线上方（prev_fast ≥ prev_slow）
     * 现在快线在慢线下方（fast < slow）
     * → 快线从上往下穿越慢线
     */
    bool death_cross = (prev_fast_ma_ >= prev_slow_ma_) && (fast_ma < slow_ma);

    if (golden_cross && !ctx.has_position()) {
        /*
         * 金叉且没有持仓 → 买入
         *
         * 计算买入数量：
         * 1. 可用资金 × 仓位比例 / 当前价格 = 大概能买多少股
         * 2. 向下取整到 100 的整数倍（A 股要求按"手"交易，1 手 = 100 股）
         *
         * static_cast<int>：把 double 转为 int（截断小数部分）
         */
        double available = ctx.cash * position_pct_;
        // 一手股数由引擎按市场给（A股 100 / crypto 1）——⛔ 别再手写 100
        int qty = ctx.lot_floor(available / ctx.current_bar.close);

        if (qty > 0) {
            orders.push_back(Order::market_buy(ctx.symbol, qty));
        }
    }
    else if (death_cross && ctx.has_position()) {
        // 死叉且有持仓 → 全部卖出
        orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
    }

    // 更新上一根 bar 的均线值
    prev_fast_ma_ = fast_ma;
    prev_slow_ma_ = slow_ma;

    return orders;
}

}  // namespace backtest
