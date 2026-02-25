/*
 * strategy_context.h — 策略上下文
 *
 * StrategyContext 是回测引擎传给策略的"状态快照"。
 * 每一根 K 线（每一天）都会创建一个新的 StrategyContext，
 * 包含策略做决策需要的所有信息：
 * - 当前 K 线数据
 * - 历史 K 线（可以计算均线等指标）
 * - 账户资金和持仓情况
 *
 * 设计思想：
 * 策略不应该直接访问引擎或组合的内部状态，
 * 而是通过这个"只读快照"来获取信息。
 * 这叫做"信息隐藏"，是 OOP 的重要原则。
 *
 * 知识点：
 * - const 引用 `const std::vector<Bar>&`：
 *   引用（&）避免拷贝大量数据，const 防止策略修改数据。
 */

#pragma once

#include "types.h"
#include <vector>
#include <string>
#include <numeric>     // std::accumulate — 用于求和
#include <algorithm>   // std::min_element, std::max_element

namespace backtest {

struct StrategyContext {
    // ── 基本信息 ──
    std::string symbol;                 // 当前股票代码
    int bar_index = 0;                  // 当前是第几根 K 线（从 0 开始）

    // ── K 线数据 ──
    Bar current_bar;                    // 当前这根 K 线
    /*
     * history 包含从第一天到当前的所有 K 线。
     * history.back() 就是 current_bar。
     * 策略可以用 history 来计算均线、RSI 等技术指标。
     *
     * const 引用：策略只能读取，不能修改历史数据。
     * 为什么用 vector 而不是数组？因为每天都会增加一根 K 线，
     * vector 可以动态增长，而数组大小在编译时就固定了。
     */
    const std::vector<Bar>* history = nullptr;

    // ── 账户状态 ──
    double cash = 0.0;                  // 可用现金
    int position_quantity = 0;          // 当前持仓量（股数）
    double position_avg_price = 0.0;    // 持仓平均成本价
    double total_value = 0.0;           // 总资产 = 现金 + 持仓市值

    // ── 便捷方法（帮助策略快速计算常用指标） ──

    /*
     * 计算简单移动平均线（SMA）
     *
     * SMA = 过去 N 天收盘价的算术平均值
     * 例如：SMA(5) = (今天 + 昨天 + 前天 + ... + 5天前) / 5
     *
     * 均线是最基本的技术指标，用来判断趋势：
     * - 价格在均线上方 → 上升趋势
     * - 价格在均线下方 → 下降趋势
     * - 短期均线上穿长期均线（"金叉"）→ 买入信号
     * - 短期均线下穿长期均线（"死叉"）→ 卖出信号
     *
     * 参数 period：计算周期（天数）
     * 返回 0.0：如果历史数据不够长
     */
    double sma(int period) const {
        if (!history || static_cast<int>(history->size()) < period) {
            return 0.0;
        }

        double sum = 0.0;
        /*
         * 从 history 的末尾往前取 period 个元素来求和。
         * history->size() - period 是起始位置。
         *
         * size_t 是无符号整数类型，用于表示大小/索引。
         * static_cast<int> 是安全的类型转换。
         */
        for (size_t i = history->size() - period; i < history->size(); ++i) {
            sum += (*history)[i].close;
        }
        return sum / period;
    }

    /*
     * 计算过去 N 天的收益率
     *
     * 收益率 = (今天的价格 - N天前的价格) / N天前的价格
     * 例如：returns(5) 表示过去 5 天的涨跌幅
     *
     * 动量策略（Momentum Strategy）就是基于这个指标：
     * 过去涨得多的股票，未来可能继续涨（趋势延续）
     */
    double returns(int lookback) const {
        if (!history || static_cast<int>(history->size()) <= lookback) {
            return 0.0;
        }
        size_t prev_idx = history->size() - 1 - lookback;
        double prev_close = (*history)[prev_idx].close;
        if (prev_close == 0.0) return 0.0;
        return (current_bar.close - prev_close) / prev_close;
    }

    /*
     * 获取最近 N 天的最高价
     */
    double highest_close(int lookback) const {
        if (!history || history->empty()) return 0.0;
        int start = std::max(0, static_cast<int>(history->size()) - lookback);
        double max_val = 0.0;
        for (int i = start; i < static_cast<int>(history->size()); ++i) {
            max_val = std::max(max_val, (*history)[i].close);
        }
        return max_val;
    }

    /*
     * 获取最近 N 天的最低价
     */
    double lowest_close(int lookback) const {
        if (!history || history->empty()) return 1e18;
        int start = std::max(0, static_cast<int>(history->size()) - lookback);
        double min_val = 1e18;
        for (int i = start; i < static_cast<int>(history->size()); ++i) {
            min_val = std::min(min_val, (*history)[i].close);
        }
        return min_val;
    }

    /*
     * 是否有持仓
     */
    bool has_position() const {
        return position_quantity > 0;
    }
};

}  // namespace backtest
