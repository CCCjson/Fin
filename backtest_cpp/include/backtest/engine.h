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
#include <map>
#include <stdexcept>

namespace backtest {

/*
 * 请求的日期区间里一根 bar 都没有。
 *
 * ⚠️ 单独一个异常类型是为了让 server 能回 **400 而不是 500** —— 这是客户端
 * 传错了窗口，不是引擎内部出错。
 * ⛔ 旧引擎遇到这种情况**静默跑全量数据**，等于对调用方撒谎（你要 2099 年，
 *    它把 2025 年的结果给你，还标着 200 OK）。
 */
struct EmptyDateRange : std::runtime_error {
    using std::runtime_error::runtime_error;
};

/*
 * BacktestResult — 回测运行的完整结果
 *
 * 包含策略信息、绩效指标、资金曲线、成交记录。
 * 会被序列化为 JSON 返回给前端。
 */
struct BacktestResult {
    std::string symbol;                             // 股票代码（多标的时是逗号连接）
    std::vector<std::string> symbols;               // 参与回测的全部标的
    std::string strategy_name;                      // 策略名称
    BacktestMetrics metrics;                        // 绩效指标
    std::vector<EquitySnapshot> equity_curve;       // 资金曲线
    std::vector<Fill> trades;                       // 成交记录
    /*
     * 回测区间**最后一天**挂起、因无「次日开盘」可成交而被丢弃的订单数。
     * 现实中同样无法执行，所以丢是对的；不静默是为了避免两次仅相差一天的回测
     * 因为这批订单消失而报出看似矛盾的成交数。
     */
    int dropped_last_bar_orders = 0;

    /*
     * **挂死**的订单数：某标的的数据在半途就断了，它那天产生的挂单再也等不到
     * 「下一根 bar 的开盘」，一路顺延到回测结束。
     * ⚠️ 与 `dropped_last_bar_orders` **分开计**：这两件事的原因完全不同，
     * 混在一起会让日志说出「最后一 bar 有 1 笔挂单被丢弃」这种假话 ——
     * 它其实是三天前就挂死了。
     */
    int dropped_stale_orders = 0;

    /*
     * 每个标的实际有多少天有 bar。
     * ⚠️ 不同标的的交易日历不一定齐（crypto 7×24 vs 股票；新币上市晚；停牌）。
     * 缺 bar 的那天该标的**不做决策**、持仓市值**沿用上一次价格** —— ⛔ 绝不当 0。
     * 把覆盖天数报出来，免得「某个币其实只有 3 天数据」这件事无声无息。
     */
    std::map<std::string, int> bar_coverage;

    /*
     * 资金竞争：同一天多个买单请求的现金合计超过账上余额的情况。
     *
     * 逐票独立回测里不存在这回事（每个标的都有完整一份本金），组合回测里天天发生。
     * 处置是**按请求名义额等比缩减**（不是先到先得 —— 那会按标的字母序系统性偏袒
     * 排在前面的，BTC 永远压着 SOL）。
     * ⛔ 不静默：削了多少天、削掉多少名义额，都要报出来。
     */
    int cash_contention_days = 0;                   // 发生缩减的天数
    double cash_contention_trimmed = 0.0;           // 被削掉的名义额合计

    /*
     * 同一标的同一天出现多根 bar 的次数（日线不该有重复日期 = 数据坏了）。
     * ⛔ 引擎只能保留一根，但不静默 —— 按 bar 下标推进的旧实现看不见这件事。
     */
    int duplicate_dates = 0;
};

class BacktestEngine {
public:
    /*
     * 构造函数
     * 接受初始资金和手续费配置
     */
    explicit BacktestEngine(double initial_capital = 100000.0,
                            CommissionConfig commission = CommissionConfig::a_share(),
                            RiskConfig risk_config = RiskConfig{},
                            MarketRules market_rules = MarketRules::a_share());

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
     * load_data — 加载某个标的的 K 线。
     *
     * std::vector<Bar> 按值传入，配合 std::move 使用：
     * 数据被"搬"进引擎，而不是"复制"一份。
     *
     * ⭐ **可以对不同 symbol 多次调用** = 组合回测（共享同一份现金）。
     * 只调一次就是单标的回测，行为与从前完全一致。
     * 同一个 symbol 调两次：后一次覆盖前一次。
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
    MarketRules market_rules_;                      // 交易单位规则（一手多少股）
    /*
     * ⚠️ 用 map 不用 unordered_map：**回测必须逐位可复现**。
     * 同一天多个标的的决策顺序由这里的遍历顺序决定，哈希表的顺序不保证稳定，
     * 那会让两次相同输入的回测给出不同结果。
     */
    std::map<std::string, std::vector<Bar>> bars_;   // symbol → K 线
    std::vector<std::string> load_order_;            // 加载顺序（结果里 symbol 字段用）
    std::unique_ptr<IStrategy> strategy_;            // 策略（独占所有权，全标的共用一个实例）
};

}  // namespace backtest
