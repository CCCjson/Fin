/*
 * combo_strategy.h — 组合策略
 *
 * 将多个子策略按权重融合信号：
 * - 每个子策略独立产生 BUY(+1)/SELL(-1)/无(0) 信号
 * - 综合信号 = sum(子信号 * 权重) / sum(权重)
 * - 综合信号 > threshold → 买入
 * - 综合信号 < -threshold → 卖出
 */

#pragma once

#include "backtest/strategy_base.h"
#include <memory>

namespace backtest {

struct SubStrategyConfig {
    std::string name;
    double weight = 1.0;
    std::unique_ptr<IStrategy> strategy;
};

class ComboStrategy : public IStrategy {
public:
    ComboStrategy(double threshold = 0.5, double position_pct = 0.95);

    void add_sub_strategy(const std::string& name, double weight,
                          std::unique_ptr<IStrategy> strategy);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    std::vector<SubStrategyConfig> sub_strategies_;
    double threshold_;
    double position_pct_;
};

}  // namespace backtest
