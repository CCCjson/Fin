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
#include <cmath>       // std::log, std::sqrt

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
    /*
     * ⚠️ 组合回测下 cash 是**全场共享的余额**（不是这个标的专属的钱）。
     * 同一天先被调用的标的花掉的钱，后面的标的就看不到了 —— 这正是
     * 「A 占了钱 B 就买不了」的资金竞争，逐票独立回测里不存在这回事。
     */
    double cash = 0.0;                  // 可用现金（全场共享）
    int position_quantity = 0;          // **本标的**当前持仓量（股数）
    double position_avg_price = 0.0;    // 本标的持仓平均成本价
    double total_value = 0.0;           // 总资产 = 现金 + 全部持仓市值
    double market_value = 0.0;          // 全部持仓市值（组合视角；单票时等于本标的市值）

    // ── 交易单位 ──
    /*
     * 一手股数。⛔ **别再手写 `/100)*100`** —— 那是 A 股假设，
     * 曾经被复制在 8 个策略里，对 crypto 直接失效（见 types.h::MarketRules）。
     * 一律用下面的 lot_floor()，有门禁盯着。
     */
    int lot_size = 100;

    /*
     * lot_floor — 把「按现金算出来的理论股数」向下取整到整手。
     * 不足一手返回 0（调用方据此不发单）。
     */
    int lot_floor(double raw_qty) const {
        int lot = lot_size > 0 ? lot_size : 1;
        if (!(raw_qty > 0.0)) return 0;
        long long lots = static_cast<long long>(raw_qty / lot);
        long long qty = lots * lot;
        // 单票回测里 raw_qty 可能极大（价格缩放后），钳到 int 上界防溢出
        if (qty > 2000000000LL) qty = 2000000000LL;
        return static_cast<int>(qty);
    }

    /*
     * afford — 用 weight 比例的可用现金能买多少（已取整到手）。
     * 把「available/close 再取整」这套重复了 8 次的算法收在一处。
     */
    int afford(double weight, double price) const {
        if (price <= 0.0) return 0;
        return lot_floor(cash * weight / price);
    }

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

    // ── 扩展技术指标 ──

    /*
     * 指数移动平均线（EMA）
     * EMA 给近期价格更高的权重，比 SMA 更灵敏。
     * 权重因子 alpha = 2 / (period + 1)
     */
    double ema(int period) const {
        if (!history || static_cast<int>(history->size()) < period) {
            return 0.0;
        }
        double alpha = 2.0 / (period + 1);
        int n = static_cast<int>(history->size());
        // 用最早的 period 个数据的 SMA 作为初始值
        double result = 0.0;
        for (int i = 0; i < period; ++i) {
            result += (*history)[i].close;
        }
        result /= period;
        // 从 period 位置开始递推
        for (int i = period; i < n; ++i) {
            result = alpha * (*history)[i].close + (1.0 - alpha) * result;
        }
        return result;
    }

    /*
     * 计算指定数据序列在特定 offset 处的 EMA（内部辅助）
     * prices: 收盘价序列
     * period: EMA 周期
     * end_idx: 计算到哪个位置（包含）
     */
    static double ema_at(const std::vector<double>& prices, int period, int end_idx) {
        if (end_idx < 0 || period <= 0) return 0.0;
        double alpha = 2.0 / (period + 1);
        int actual_start = std::min(period, end_idx + 1);
        double result = 0.0;
        for (int i = 0; i < actual_start; ++i) {
            result += prices[i];
        }
        result /= actual_start;
        for (int i = actual_start; i <= end_idx; ++i) {
            result = alpha * prices[i] + (1.0 - alpha) * result;
        }
        return result;
    }

    /*
     * MACD 指标（三线）
     * DIF = EMA(fast) - EMA(slow)
     * DEA = EMA(DIF, signal)
     * HIST = 2 * (DIF - DEA)
     */
    struct MACDResult {
        double dif = 0.0;
        double dea = 0.0;
        double hist = 0.0;
    };

    MACDResult macd(int fast_period = 12, int slow_period = 26, int signal_period = 9) const {
        MACDResult r;
        if (!history || static_cast<int>(history->size()) < slow_period) {
            return r;
        }
        int n = static_cast<int>(history->size());
        // 先算所有 bar 的收盘价
        std::vector<double> closes(n);
        for (int i = 0; i < n; ++i) {
            closes[i] = (*history)[i].close;
        }
        // 计算每个 bar 的 DIF
        std::vector<double> dif_series(n, 0.0);
        double alpha_fast = 2.0 / (fast_period + 1);
        double alpha_slow = 2.0 / (slow_period + 1);
        // EMA fast
        std::vector<double> ema_fast_arr(n, 0.0);
        ema_fast_arr[0] = closes[0];
        for (int i = 1; i < n; ++i) {
            ema_fast_arr[i] = alpha_fast * closes[i] + (1.0 - alpha_fast) * ema_fast_arr[i - 1];
        }
        // EMA slow
        std::vector<double> ema_slow_arr(n, 0.0);
        ema_slow_arr[0] = closes[0];
        for (int i = 1; i < n; ++i) {
            ema_slow_arr[i] = alpha_slow * closes[i] + (1.0 - alpha_slow) * ema_slow_arr[i - 1];
        }
        // DIF = EMA(fast) - EMA(slow)
        for (int i = 0; i < n; ++i) {
            dif_series[i] = ema_fast_arr[i] - ema_slow_arr[i];
        }
        // DEA = EMA(DIF, signal_period)
        double alpha_sig = 2.0 / (signal_period + 1);
        std::vector<double> dea_series(n, 0.0);
        dea_series[0] = dif_series[0];
        for (int i = 1; i < n; ++i) {
            dea_series[i] = alpha_sig * dif_series[i] + (1.0 - alpha_sig) * dea_series[i - 1];
        }
        r.dif = dif_series[n - 1];
        r.dea = dea_series[n - 1];
        r.hist = 2.0 * (r.dif - r.dea);
        return r;
    }

    /*
     * RSI（相对强弱指标）
     * RSI = 100 - 100/(1 + RS)，RS = 平均涨幅 / 平均跌幅
     * RSI < 30 → 超卖（买入信号）
     * RSI > 70 → 超买（卖出信号）
     */
    double rsi(int period = 14) const {
        if (!history || static_cast<int>(history->size()) < period + 1) {
            return 50.0; // 数据不足返回中性值
        }
        int n = static_cast<int>(history->size());
        // 计算价格变动
        std::vector<double> changes(n - 1);
        for (int i = 1; i < n; ++i) {
            changes[i - 1] = (*history)[i].close - (*history)[i - 1].close;
        }
        // Wilder's smoothing（指数移动平均）
        double avg_gain = 0.0, avg_loss = 0.0;
        // 初始 period 个变化的平均
        for (int i = 0; i < period && i < static_cast<int>(changes.size()); ++i) {
            if (changes[i] > 0) avg_gain += changes[i];
            else avg_loss += (-changes[i]);
        }
        avg_gain /= period;
        avg_loss /= period;
        // 递推
        for (int i = period; i < static_cast<int>(changes.size()); ++i) {
            double gain = changes[i] > 0 ? changes[i] : 0.0;
            double loss = changes[i] < 0 ? -changes[i] : 0.0;
            avg_gain = (avg_gain * (period - 1) + gain) / period;
            avg_loss = (avg_loss * (period - 1) + loss) / period;
        }
        if (avg_loss == 0.0) return 100.0;
        double rs = avg_gain / avg_loss;
        return 100.0 - 100.0 / (1.0 + rs);
    }

    /*
     * KDJ 指标
     * RSV = (Close - LLV(N)) / (HHV(N) - LLV(N)) * 100
     * K = EMA(RSV, m1)  （实际是 SMA 平滑）
     * D = EMA(K, m2)
     * J = 3K - 2D
     */
    struct KDJResult {
        double k = 50.0;
        double d = 50.0;
        double j = 50.0;
    };

    KDJResult kdj(int n = 9, int m1 = 3, int m2 = 3) const {
        KDJResult r;
        if (!history || static_cast<int>(history->size()) < n) {
            return r;
        }
        int len = static_cast<int>(history->size());
        double k_val = 50.0, d_val = 50.0;
        // 从第 n-1 根 bar 开始逐日计算
        for (int i = n - 1; i < len; ++i) {
            // 最近 n 天的最高价和最低价
            double hhv = (*history)[i].high;
            double llv = (*history)[i].low;
            for (int j = i - n + 1; j < i; ++j) {
                hhv = std::max(hhv, (*history)[j].high);
                llv = std::min(llv, (*history)[j].low);
            }
            double rsv = (hhv == llv) ? 50.0 :
                ((*history)[i].close - llv) / (hhv - llv) * 100.0;
            // SMA 平滑
            k_val = (rsv + (m1 - 1) * k_val) / m1;
            d_val = (k_val + (m2 - 1) * d_val) / m2;
        }
        r.k = k_val;
        r.d = d_val;
        r.j = 3.0 * k_val - 2.0 * d_val;
        return r;
    }

    /*
     * 布林带（Bollinger Bands）
     * 中轨 = SMA(period)
     * 上轨 = 中轨 + num_std × 标准差
     * 下轨 = 中轨 - num_std × 标准差
     */
    struct BollingerResult {
        double upper = 0.0;
        double middle = 0.0;
        double lower = 0.0;
    };

    BollingerResult bollinger(int period = 20, double num_std = 2.0) const {
        BollingerResult r;
        r.middle = sma(period);
        if (r.middle == 0.0) return r;
        double sd = stddev(period);
        r.upper = r.middle + num_std * sd;
        r.lower = r.middle - num_std * sd;
        return r;
    }

    /*
     * 标准差
     * 衡量价格波动的离散程度
     */
    double stddev(int period) const {
        if (!history || static_cast<int>(history->size()) < period) {
            return 0.0;
        }
        double mean = sma(period);
        double sum_sq = 0.0;
        for (size_t i = history->size() - period; i < history->size(); ++i) {
            double diff = (*history)[i].close - mean;
            sum_sq += diff * diff;
        }
        return std::sqrt(sum_sq / period);
    }
};

}  // namespace backtest
