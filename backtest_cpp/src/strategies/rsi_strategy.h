/*
 * rsi_strategy.h — RSI 超买超卖策略
 *
 * RSI < oversold（默认30）回升 → 买入
 * RSI > overbought（默认70）回落 → 卖出
 */

#pragma once

#include "backtest/strategy_base.h"

namespace backtest {

class RSIStrategy : public IStrategy {
public:
    RSIStrategy(int period = 14, double oversold = 30.0,
                double overbought = 70.0, double position_pct = 0.95);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    int period_;
    double oversold_;
    double overbought_;
    double position_pct_;

    double prev_rsi_ = 50.0;
};

}  // namespace backtest
