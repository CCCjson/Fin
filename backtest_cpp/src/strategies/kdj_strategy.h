/*
 * kdj_strategy.h — KDJ 金叉/死叉策略
 *
 * K 上穿 D（低位区域）→ 买入
 * K 下穿 D（高位区域）→ 卖出
 */

#pragma once

#include "backtest/strategy_base.h"

namespace backtest {

class KDJStrategy : public IStrategy {
public:
    KDJStrategy(int n = 9, int m1 = 3, int m2 = 3,
                double oversold = 20.0, double overbought = 80.0,
                double position_pct = 0.95);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    int n_, m1_, m2_;
    double oversold_;
    double overbought_;
    double position_pct_;

    double prev_k_ = 50.0;
    double prev_d_ = 50.0;
};

}  // namespace backtest
