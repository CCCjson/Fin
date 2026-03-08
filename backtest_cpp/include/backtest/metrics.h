/*
 * metrics.h — 绩效指标计算
 *
 * 回测结束后，我们需要评价策略的好坏。
 * 不能只看"赚了多少钱"，还要看"承受了多少风险"。
 *
 * 核心指标：
 * - 收益率：赚了多少？
 * - 最大回撤：最大亏过多少？（衡量风险）
 * - 夏普比率：每承受 1 单位风险，获得多少收益？
 * - 胜率：多少笔交易是赚钱的？
 *
 * 这些指标在量化金融面试中经常被问到。
 */

#pragma once

#include "types.h"
#include <vector>
#include <string>

namespace backtest {

/*
 * BacktestMetrics — 回测结果的所有指标
 *
 * 把所有指标打包在一个 struct 里，方便传递和序列化为 JSON。
 */
struct BacktestMetrics {
    // ── 收益指标 ──
    double total_return = 0.0;          // 总收益率（如 0.25 = 25%）
    double annualized_return = 0.0;     // 年化收益率
    double final_value = 0.0;           // 最终资产

    // ── 风险指标 ──
    double volatility = 0.0;            // 年化波动率
    double max_drawdown = 0.0;          // 最大回撤（如 0.15 = 15%）
    double max_drawdown_amount = 0.0;   // 最大回撤金额
    std::string max_dd_start_date;      // 回撤开始日期（峰值）
    std::string max_dd_end_date;        // 回撤结束日期（谷值）

    // ── 风险调整收益 ──
    double sharpe_ratio = 0.0;          // 夏普比率
    double sortino_ratio = 0.0;         // 索提诺比率

    // ── 交易统计 ──
    int total_trades = 0;               // 总交易次数
    int winning_trades = 0;             // 盈利交易次数
    int losing_trades = 0;              // 亏损交易次数
    double win_rate = 0.0;              // 胜率（如 0.60 = 60%）
    double profit_factor = 0.0;         // 盈亏比（总盈利 / 总亏损）
    double avg_profit = 0.0;            // 平均盈利金额
    double avg_loss = 0.0;              // 平均亏损金额
    double total_commission = 0.0;      // 总手续费
    double total_slippage = 0.0;        // 总滑点成本
};

/*
 * Metrics — 绩效计算器（静态工具类）
 *
 * 所有方法都是 static 的，不需要创建对象就可以调用：
 * Metrics::calculate(equity_curve, fills, ...)
 *
 * 为什么用 static？
 * 因为 Metrics 没有内部状态，它只是一组计算函数的集合。
 * 这种没有状态的工具类，用 static 方法最合适。
 */
class Metrics {
public:
    /*
     * calculate — 计算所有指标
     * 这是主入口，内部会调用其他所有方法。
     */
    static BacktestMetrics calculate(
        const std::vector<EquitySnapshot>& equity_curve,
        const std::vector<Fill>& fills,
        double initial_capital,
        double risk_free_rate = 0.03    // 无风险利率，默认 3%
    );

    // ── 单独的计算方法（也可以单独使用） ──

    static double calc_total_return(const std::vector<EquitySnapshot>& curve,
                                     double initial_capital);

    static double calc_annualized_return(double total_return, int trading_days);

    static double calc_volatility(const std::vector<EquitySnapshot>& curve);

    static double calc_sharpe_ratio(const std::vector<EquitySnapshot>& curve,
                                     double risk_free_rate);

    static double calc_sortino_ratio(const std::vector<EquitySnapshot>& curve,
                                      double risk_free_rate);

    /*
     * MaxDrawdown 结构体：封装最大回撤的详细信息
     */
    struct MaxDrawdown {
        double drawdown_pct = 0.0;      // 回撤百分比
        double drawdown_amount = 0.0;   // 回撤金额
        std::string start_date;         // 峰值日期
        std::string end_date;           // 谷值日期
    };

    static MaxDrawdown calc_max_drawdown(const std::vector<EquitySnapshot>& curve);

    /*
     * TradeStats：交易统计
     * 配对买入和卖出，计算每笔交易的盈亏
     */
    struct TradeStats {
        int total = 0;
        int winners = 0;
        int losers = 0;
        double win_rate = 0.0;
        double profit_factor = 0.0;
        double avg_profit = 0.0;
        double avg_loss = 0.0;
    };

    static TradeStats calc_trade_stats(const std::vector<Fill>& fills);
};

}  // namespace backtest
