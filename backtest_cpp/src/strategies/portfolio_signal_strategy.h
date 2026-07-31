/*
 * portfolio_signal_strategy.h — 组合信号回放策略（S8）
 *
 * `ExternalSignalStrategy` 只有**一张**按日期的信号表，所以它只能回放单个标的。
 * 组合回测里每个标的各有一条信号序列，需要按 `ctx.symbol` 分发。
 *
 * ⭐ 关键点在于**它不管资金分配**：每天每个标的各被调一次 on_bar，
 * `ctx.cash` 是**全场共享的当前余额** —— 前面标的花掉的钱这里就看不到了。
 * 真正的「谁买得到」由引擎的资金竞争缩减决定（见 engine.cpp），
 * 策略这一层只负责说「我想买 weight 比例」。
 *
 * ⛔ 别在这里做「按权重切资金」：那样每个标的又变成拿着自己那份钱各买各的，
 *    等于把共享资金池退回成 N 个独立钱包 —— 正是 S8 要解决的那个问题。
 */

#pragma once

#include "backtest/strategy_base.h"
// SignalEntry 定义在这里（单标的信号回放）——组合版复用同一个结构，别再定义第二份
#include "external_signal_strategy.h"
#include <map>
#include <string>

namespace backtest {

class PortfolioSignalStrategy : public IStrategy {
public:
    /* symbol → (date → 信号)。同一标的同一天最多一条（解析时后者覆盖前者）。 */
    explicit PortfolioSignalStrategy(
        std::map<std::string, std::map<std::string, SignalEntry>> by_symbol);

    std::string name() const override;
    std::string description() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    std::map<std::string, std::map<std::string, SignalEntry>> by_symbol_;
};

}  // namespace backtest
