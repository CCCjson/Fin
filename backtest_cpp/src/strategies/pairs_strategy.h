/*
 * pairs_strategy.h — 统计套利（配对交易）策略
 *
 * 利用两只股票的价差均值回归特性：
 * - z-score = (当前价差 - 均值) / 标准差
 * - z < -entry_z → 买入A（做多价差）
 * - z > entry_z → 卖出A（做空价差）
 * - |z| < exit_z → 平仓
 *
 * 注意：不修改 BacktestEngine，策略构造时接收第二只股票的全部 bars
 */

#pragma once

#include "backtest/strategy_base.h"
#include <cmath>

namespace backtest {

class PairsStrategy : public IStrategy {
public:
    PairsStrategy(const std::string& symbol2,
                  const std::vector<Bar>& bars2,
                  int lookback = 60,
                  double entry_z = 2.0,
                  double exit_z = 0.5,
                  double position_pct = 0.95);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    std::string symbol2_;
    std::vector<Bar> bars2_;
    int lookback_;
    double entry_z_;
    double exit_z_;
    double position_pct_;
};

}  // namespace backtest
