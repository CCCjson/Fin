/*
 * macd_strategy.h — MACD 交叉策略
 *
 * MACD（Moving Average Convergence Divergence）是最常用的趋势跟踪指标。
 *
 * 信号：
 * - DIF 上穿 DEA → 买入（金叉）
 * - DIF 下穿 DEA → 卖出（死叉）
 */

#pragma once

#include "backtest/strategy_base.h"

namespace backtest {

class MACDStrategy : public IStrategy {
public:
    MACDStrategy(int fast_period = 12, int slow_period = 26,
                 int signal_period = 9, double position_pct = 0.95);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    int fast_period_;
    int slow_period_;
    int signal_period_;
    double position_pct_;

    double prev_dif_ = 0.0;
    double prev_dea_ = 0.0;
    bool initialized_ = false;
};

}  // namespace backtest
