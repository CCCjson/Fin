/*
 * momentum_strategy.h — 动量策略
 *
 * 动量（Momentum）是金融学中最著名的因子之一。
 *
 * 核心思想："强者恒强"
 * 过去一段时间涨得多的股票，未来可能继续涨（趋势延续）。
 * 反之，跌得多的可能继续跌。
 *
 * 策略逻辑：
 * - 过去 N 天涨幅 > 买入阈值 → 买入（趋势向上）
 * - 过去 N 天涨幅 < 卖出阈值 → 卖出（趋势反转或向下）
 *
 * 参数：
 * - lookback：回看周期（默认 20 天）
 * - buy_threshold：买入阈值（默认 5% 涨幅）
 * - sell_threshold：卖出阈值（默认 -3% 跌幅）
 *
 * 知识点：
 * - 继承 public IStrategy：公有继承，
 *   IStrategy 的公有方法在 MomentumStrategy 中仍是公有的。
 *   这是多态的基础。
 */

#pragma once

#include "backtest/strategy_base.h"

namespace backtest {

class MomentumStrategy : public IStrategy {
public:
    MomentumStrategy(int lookback = 20,
                     double buy_threshold = 0.05,
                     double sell_threshold = -0.03,
                     double position_pct = 0.95);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    int lookback_;                  // 回看周期（天数）
    double buy_threshold_;          // 买入阈值（涨幅百分比）
    double sell_threshold_;         // 卖出阈值（跌幅百分比）
    double position_pct_;           // 仓位比例
};

}  // namespace backtest
