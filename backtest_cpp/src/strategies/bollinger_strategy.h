/*
 * bollinger_strategy.h — 布林带策略
 *
 * 价格触及下轨反弹 → 买入
 * 价格触及上轨回落 → 卖出
 */

#pragma once

#include "backtest/strategy_base.h"

namespace backtest {

class BollingerStrategy : public IStrategy {
public:
    BollingerStrategy(int period = 20, double num_std = 2.0,
                      double position_pct = 0.95);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    int period_;
    double num_std_;
    double position_pct_;

    double prev_close_ = 0.0;
    double prev_lower_ = 0.0;
    double prev_upper_ = 0.0;
};

}  // namespace backtest
