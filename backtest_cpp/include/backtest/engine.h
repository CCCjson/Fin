/*
 * engine.h — 回测引擎核心
 *
 * BacktestEngine 是整个系统的"总指挥"，负责：
 * 1. 加载数据
 * 2. 逐 bar（逐天）驱动策略
 * 3. 执行策略产生的订单
 * 4. 记录每日净值
 * 5. 计算绩效指标
 *
 * 核心循环（伪代码）：
 *   for (每一天) {
 *       更新持仓价格
 *       构建 StrategyContext
 *       orders = strategy->on_bar(context)
 *       执行所有订单
 *       记录净值
 *   }
 *
 * 知识点：
 * - std::unique_ptr<IStrategy>：
 *   独占智能指针，拥有策略对象的唯一所有权。
 *   当 engine 被销毁时，策略对象也会被自动销毁。
 *   不能复制，只能移动（std::move）。
 */

#pragma once

#include "types.h"
#include "portfolio.h"
#include "strategy_base.h"
#include "metrics.h"
#include "risk_manager.h"
#include <memory>      // unique_ptr
#include <vector>
#include <string>

namespace backtest {

/*
 * BacktestResult — 回测运行的完整结果
 *
 * 包含策略信息、绩效指标、资金曲线、成交记录。
 * 会被序列化为 JSON 返回给前端。
 */
struct BacktestResult {
    std::string symbol;                             // 股票代码
    std::string strategy_name;                      // 策略名称
    BacktestMetrics metrics;                        // 绩效指标
    std::vector<EquitySnapshot> equity_curve;       // 资金曲线
    std::vector<Fill> trades;                       // 成交记录
};

class BacktestEngine {
public:
    /*
     * 构造函数
     * 接受初始资金和手续费配置
     */
    explicit BacktestEngine(double initial_capital = 100000.0,
                            CommissionConfig commission = CommissionConfig::a_share(),
                            RiskConfig risk_config = RiskConfig{});

    /*
     * set_strategy — 设置要回测的策略
     *
     * 使用 std::unique_ptr 传递策略的所有权。
     * 调用者创建策略后，通过 std::move 转移给引擎。
     *
     * 为什么用 unique_ptr？
     * 1. 自动内存管理：引擎销毁时策略也被销毁
     * 2. 所有权清晰：只有引擎"拥有"这个策略
     * 3. 多态：unique_ptr<IStrategy> 可以指向任何具体策略
     *
     * 使用方式：
     *   engine.set_strategy(std::make_unique<MACrossStrategy>(5, 20));
     */
    void set_strategy(std::unique_ptr<IStrategy> strategy);

    /*
     * load_data — 加载 K 线数据
     *
     * std::vector<Bar>&& ：右值引用，配合 std::move 使用。
     * 数据被"搬"进引擎，而不是"复制"一份。
     * 对于大量数据（几千根 K 线），这可以省下不少时间和内存。
     */
    void load_data(const std::string& symbol, std::vector<Bar> bars);

    /*
     * run — 运行回测
     *
     * 这是核心方法，执行完整的回测循环。
     * start_date / end_date：可选的日期范围过滤。
     *
     * 返回 BacktestResult：包含所有结果。
     */
    BacktestResult run(const std::string& start_date = "",
                       const std::string& end_date = "");

private:
    double initial_capital_;                        // 初始资金
    CommissionConfig commission_config_;             // 手续费配置
    RiskConfig risk_config_;                        // 风控配置
    std::string symbol_;                            // 股票代码
    std::vector<Bar> bars_;                         // K 线数据
    std::unique_ptr<IStrategy> strategy_;            // 策略（独占所有权）
};

}  // namespace backtest
