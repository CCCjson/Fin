/*
 * ma_cross_strategy.h — 均线交叉策略
 *
 * 这是最经典的技术分析策略之一。
 *
 * 原理：
 * 使用两条移动平均线：一条"快线"（短周期），一条"慢线"（长周期）。
 * - 当快线从下方穿越慢线（"金叉"）→ 买入信号
 * - 当快线从上方穿越慢线（"死叉"）→ 卖出信号
 *
 * 为什么有效？
 * 快线反映近期趋势，慢线反映长期趋势。
 * 金叉意味着"近期开始走强"，死叉意味着"近期开始走弱"。
 *
 * 参数：
 * - fast_period：快线周期（默认 5 天）
 * - slow_period：慢线周期（默认 20 天）
 * - position_pct：仓位比例（默认 95% 的资金）
 *
 * 知识点：
 * - override：明确表示这个方法是重写父类的虚函数
 *   如果打错了方法名，编译器会报错，帮你抓 bug
 * - final：阻止子类进一步重写这个方法（可选）
 */

#pragma once

#include "backtest/strategy_base.h"

namespace backtest {

class MACrossStrategy : public IStrategy {
public:
    /*
     * 构造函数
     * 接受策略参数，有默认值。
     *
     * 默认 fast=5, slow=20：这是最常用的参数组合之一。
     * 也可以试 (10, 30)、(20, 60) 等。
     */
    MACrossStrategy(int fast_period = 5, int slow_period = 20, double position_pct = 0.95);

    // ── 实现 IStrategy 接口 ──

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    int fast_period_;       // 快线周期
    int slow_period_;       // 慢线周期
    double position_pct_;   // 仓位比例

    // 上一根 bar 的均线值（用于判断"穿越"）
    double prev_fast_ma_ = 0.0;
    double prev_slow_ma_ = 0.0;
};

}  // namespace backtest
