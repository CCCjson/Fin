/*
 * metrics.cpp — 绩效指标的具体实现
 *
 * 这里实现了量化金融中最核心的一组评估指标。
 * 每个指标都有金融含义和数学公式，注释里会详细解释。
 */

#include "backtest/metrics.h"
#include <cmath>       // sqrt, pow, log
#include <numeric>     // accumulate
#include <algorithm>   // max, min
#include <map>

namespace backtest {

/*
 * calc_total_return — 总收益率
 *
 * 公式：(最终资产 - 初始资金) / 初始资金
 * 例如：初始 100 万，最终 125 万 → 收益率 = 25%
 */
double Metrics::calc_total_return(const std::vector<EquitySnapshot>& curve,
                                   double initial_capital) {
    if (curve.empty() || initial_capital <= 0) return 0.0;
    return (curve.back().total_value - initial_capital) / initial_capital;
}

/*
 * calc_annualized_return — 年化收益率
 *
 * 为什么需要年化？
 * 比如策略 A 在 6 个月赚了 10%，策略 B 在 2 年赚了 30%。
 * 哪个更好？直接比较不公平，需要换算成"每年赚多少"。
 *
 * 公式：(1 + 总收益率) ^ (252 / 交易天数) - 1
 *
 * 252 是一年的交易日数量（股市不是每天开门的，要去掉周末和节假日）
 *
 * pow(x, y) 是 x 的 y 次方。
 * 为什么用幂函数而不是简单乘法？
 * 因为投资有"复利效应"，每天的收益会被"利滚利"。
 */
double Metrics::calc_annualized_return(double total_return, int trading_days) {
    if (trading_days <= 0) return 0.0;
    // 252 个交易日 = 一年
    return std::pow(1.0 + total_return, 252.0 / trading_days) - 1.0;
}

/*
 * calc_volatility — 年化波动率
 *
 * 波动率衡量"收益的不确定性"，越高表示风险越大。
 *
 * 计算步骤：
 * 1. 先算每天的收益率（daily_return 已经在 equity_curve 里了）
 * 2. 算标准差（衡量数据的分散程度）
 * 3. 乘以 √252 得到年化波动率
 *
 * 标准差公式：σ = √(Σ(xi - μ)² / n)
 * 其中 μ 是平均值，xi 是每个数据点，n 是数据量。
 *
 * 为什么乘 √252？
 * 因为日收益率的方差 × 252 = 年方差（假设每天独立），
 * 标准差 = √方差，所以 σ_年 = σ_日 × √252
 */
double Metrics::calc_volatility(const std::vector<EquitySnapshot>& curve) {
    if (curve.size() < 2) return 0.0;

    // 收集所有日收益率
    std::vector<double> returns;
    for (size_t i = 1; i < curve.size(); ++i) {
        returns.push_back(curve[i].daily_return);
    }

    // 计算平均收益率
    double sum = std::accumulate(returns.begin(), returns.end(), 0.0);
    double mean = sum / returns.size();

    // 计算方差 = Σ(xi - μ)² / n
    double variance = 0.0;
    for (double r : returns) {
        double diff = r - mean;
        variance += diff * diff;
    }
    variance /= returns.size();

    // 年化波动率 = 日标准差 × √252
    return std::sqrt(variance) * std::sqrt(252.0);
}

/*
 * calc_sharpe_ratio — 夏普比率 (Sharpe Ratio)
 *
 * 这是量化金融中最重要的指标之一，由诺贝尔奖得主 William Sharpe 提出。
 *
 * 核心思想：
 * 不能只看收益，还要看承担了多少风险。
 * 高收益 + 高风险 未必比 中收益 + 低风险 好。
 *
 * 公式：Sharpe = (年化收益 - 无风险利率) / 年化波动率
 *
 * 无风险利率（risk_free_rate）：
 * 把钱存银行或买国债的收益率，大约 3%。
 * Sharpe 衡量的是"超额收益"（比无风险投资多赚的部分）相对于风险的比值。
 *
 * 解读：
 * - Sharpe > 2.0：非常优秀
 * - Sharpe > 1.0：不错
 * - Sharpe < 0.5：一般
 * - Sharpe < 0：还不如把钱存银行
 */
double Metrics::calc_sharpe_ratio(const std::vector<EquitySnapshot>& curve,
                                   double risk_free_rate) {
    if (curve.size() < 2) return 0.0;

    // 收集日收益率
    std::vector<double> returns;
    for (size_t i = 1; i < curve.size(); ++i) {
        returns.push_back(curve[i].daily_return);
    }

    // 日均无风险利率
    double daily_rf = risk_free_rate / 252.0;

    // 日均超额收益
    double sum = 0.0;
    for (double r : returns) {
        sum += (r - daily_rf);
    }
    double mean_excess = sum / returns.size();

    // 超额收益的标准差
    double variance = 0.0;
    for (double r : returns) {
        double diff = (r - daily_rf) - mean_excess;
        variance += diff * diff;
    }
    variance /= returns.size();
    double std_dev = std::sqrt(variance);

    if (std_dev == 0.0) return 0.0;

    // 年化 Sharpe = 日均超额收益 / 日标准差 × √252
    return (mean_excess / std_dev) * std::sqrt(252.0);
}

/*
 * calc_sortino_ratio — 索提诺比率 (Sortino Ratio)
 *
 * Sharpe 比率的改进版。
 * Sharpe 惩罚所有波动（包括向上的波动，即大赚的日子）。
 * Sortino 只惩罚"下行波动"（亏钱的日子）。
 *
 * 公式：Sortino = (年化收益 - 无风险利率) / 下行标准差
 *
 * "下行标准差"只用负收益来计算。
 * 这更合理：我们不应该因为"赚太多"而降低评分。
 */
double Metrics::calc_sortino_ratio(const std::vector<EquitySnapshot>& curve,
                                    double risk_free_rate) {
    if (curve.size() < 2) return 0.0;

    double daily_rf = risk_free_rate / 252.0;

    std::vector<double> returns;
    for (size_t i = 1; i < curve.size(); ++i) {
        returns.push_back(curve[i].daily_return);
    }

    // 日均超额收益
    double sum = 0.0;
    for (double r : returns) {
        sum += (r - daily_rf);
    }
    double mean_excess = sum / returns.size();

    // 下行方差：只计算负超额收益
    double downside_variance = 0.0;
    int downside_count = 0;
    for (double r : returns) {
        double excess = r - daily_rf;
        if (excess < 0) {
            downside_variance += excess * excess;
            downside_count++;
        }
    }

    if (downside_count == 0) return 0.0;  // 没有亏损日，Sortino 无意义
    downside_variance /= returns.size();   // 用总天数做分母（标准做法）

    double downside_std = std::sqrt(downside_variance);
    if (downside_std == 0.0) return 0.0;

    return (mean_excess / downside_std) * std::sqrt(252.0);
}

/*
 * calc_max_drawdown — 最大回撤
 *
 * 最大回撤是风险管理中最重要的指标之一。
 * 它回答了一个问题："如果我在最差的时间点买入，最多会亏多少？"
 *
 * 计算方法：
 * 1. 遍历每一天的总资产
 * 2. 记录到当前为止的最高值（peak）
 * 3. 计算当前值相对于 peak 的跌幅
 * 4. 找出最大的跌幅
 *
 * 例如：
 * 资金曲线：100 → 110 → 105 → 90 → 95 → 120
 *                   peak=110       最大回撤在这里：(110-90)/110 = 18.2%
 *
 * 面试常问："你的策略最大回撤是多少？"
 * 一般要求最大回撤 < 20%。
 */
Metrics::MaxDrawdown Metrics::calc_max_drawdown(const std::vector<EquitySnapshot>& curve) {
    MaxDrawdown result;
    if (curve.empty()) return result;

    double peak = curve[0].total_value;
    int peak_idx = 0;
    int trough_idx = 0;

    for (size_t i = 1; i < curve.size(); ++i) {
        double value = curve[i].total_value;

        if (value > peak) {
            // 创新高，更新 peak
            peak = value;
            peak_idx = static_cast<int>(i);
        } else {
            // 计算当前回撤
            double dd_pct = (peak - value) / peak;
            double dd_amount = peak - value;

            if (dd_pct > result.drawdown_pct) {
                // 发现更大的回撤
                result.drawdown_pct = dd_pct;
                result.drawdown_amount = dd_amount;
                result.start_date = curve[peak_idx].date;
                result.end_date = curve[i].date;
            }
        }
    }

    return result;
}

/*
 * calc_trade_stats — 交易统计
 *
 * 把买入和卖出配对，计算每笔"完整交易"的盈亏。
 *
 * 配对方法：
 * 对于每只股票，按时间顺序将买入和卖出配对。
 * 收益 = 卖出金额 - 买入金额 - 手续费
 * > 0 = 盈利交易，< 0 = 亏损交易
 *
 * 盈亏比 (Profit Factor) = 所有盈利的总和 / 所有亏损的总和
 * > 1 表示盈利大于亏损，< 1 表示亏损大于盈利。
 */
Metrics::TradeStats Metrics::calc_trade_stats(const std::vector<Fill>& fills) {
    TradeStats stats;

    /*
     * 按股票分组，收集每只股票的买入和卖出
     * std::map<string, vector<Fill>>：key=股票代码, value=该股票的成交列表
     */
    std::map<std::string, std::vector<const Fill*>> buys, sells;
    for (const auto& f : fills) {
        if (f.side == Side::BUY) {
            buys[f.symbol].push_back(&f);
        } else {
            sells[f.symbol].push_back(&f);
        }
    }

    double total_profit = 0.0;     // 盈利交易的总盈利
    double total_loss = 0.0;       // 亏损交易的总亏损（正数）
    int win_count = 0;
    int loss_count = 0;

    // 配对买入和卖出
    for (auto& [symbol, sell_list] : sells) {
        auto& buy_list = buys[symbol];
        size_t buy_idx = 0;

        for (const auto* sell : sell_list) {
            if (buy_idx >= buy_list.size()) break;
            const auto* buy = buy_list[buy_idx];
            buy_idx++;

            // 计算这笔交易的盈亏
            double buy_cost = buy->price * buy->quantity + buy->commission;
            double sell_revenue = sell->price * sell->quantity - sell->commission;
            double pnl = sell_revenue - buy_cost;

            if (pnl > 0) {
                win_count++;
                total_profit += pnl;
            } else {
                loss_count++;
                total_loss += std::abs(pnl);
            }
        }
    }

    stats.total = win_count + loss_count;
    stats.winners = win_count;
    stats.losers = loss_count;
    stats.win_rate = stats.total > 0 ? static_cast<double>(win_count) / stats.total : 0.0;
    stats.profit_factor = total_loss > 0 ? total_profit / total_loss : 0.0;
    stats.avg_profit = win_count > 0 ? total_profit / win_count : 0.0;
    stats.avg_loss = loss_count > 0 ? total_loss / loss_count : 0.0;

    return stats;
}

/*
 * calculate — 计算所有指标（主入口）
 *
 * 调用上面所有的子方法，汇总成 BacktestMetrics 结构体。
 */
BacktestMetrics Metrics::calculate(const std::vector<EquitySnapshot>& equity_curve,
                                    const std::vector<Fill>& fills,
                                    double initial_capital,
                                    double risk_free_rate) {
    BacktestMetrics m;

    // 收益指标
    m.total_return = calc_total_return(equity_curve, initial_capital);
    m.annualized_return = calc_annualized_return(
        m.total_return, static_cast<int>(equity_curve.size()));
    m.final_value = equity_curve.empty() ? initial_capital : equity_curve.back().total_value;

    // 风险指标
    m.volatility = calc_volatility(equity_curve);
    auto dd = calc_max_drawdown(equity_curve);
    m.max_drawdown = dd.drawdown_pct;
    m.max_drawdown_amount = dd.drawdown_amount;
    m.max_dd_start_date = dd.start_date;
    m.max_dd_end_date = dd.end_date;

    // 风险调整收益
    m.sharpe_ratio = calc_sharpe_ratio(equity_curve, risk_free_rate);
    m.sortino_ratio = calc_sortino_ratio(equity_curve, risk_free_rate);

    // 交易统计
    auto ts = calc_trade_stats(fills);
    m.total_trades = ts.total;
    m.winning_trades = ts.winners;
    m.losing_trades = ts.losers;
    m.win_rate = ts.win_rate;
    m.profit_factor = ts.profit_factor;
    m.avg_profit = ts.avg_profit;
    m.avg_loss = ts.avg_loss;

    // 手续费
    double total_comm = 0.0;
    for (const auto& f : fills) {
        total_comm += f.commission;
    }
    m.total_commission = total_comm;

    return m;
}

}  // namespace backtest
