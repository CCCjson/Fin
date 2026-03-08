/*
 * portfolio.h — 投资组合管理
 *
 * Portfolio 是回测系统的"钱包"，负责：
 * 1. 管理现金（初始资金、买入扣钱、卖出收钱）
 * 2. 管理持仓（买入建仓、卖出平仓、更新市值）
 * 3. 记录成交历史（用于计算胜率、盈亏比）
 * 4. 记录每日净值（用于绘制资金曲线）
 *
 * 设计思想：
 * Portfolio 对标 Python 版的 Portfolio 类，但用 C++ 重写
 * 关键区别：C++ 需要手动管理内存和类型，更接近底层。
 *
 * 知识点：
 * - std::map：有序映射（红黑树），按 key 排序
 *   适合持仓表：可以按股票代码排序遍历
 * - std::optional (C++17)：可能有值也可能没值
 *   比用 nullptr 或 -1 表示"没有"更安全
 */

#pragma once

#include "types.h"
#include <map>
#include <vector>
#include <string>
#include <optional>    // C++17：表示"可能有值"的类型

namespace backtest {

class Portfolio {
public:
    /*
     * 构造函数
     * explicit：防止隐式类型转换
     * 比如不允许 Portfolio p = 100000.0; 这种写法
     * 必须写 Portfolio p(100000.0);
     */
    explicit Portfolio(double initial_capital = 100000.0,
                       CommissionConfig commission = CommissionConfig::a_share());

    // ── 订单执行 ──

    /*
     * execute_order — 执行一个订单
     *
     * 参数：
     * - order：要执行的订单
     * - current_price：当前市场价格（市价单用这个价成交）
     * - date：成交日期
     *
     * 返回：
     * - std::optional<Fill>：如果成交返回 Fill，否则返回 std::nullopt
     *
     * std::optional 是 C++17 的一个很有用的类型：
     * - 有值时：opt.has_value() == true, *opt 取值
     * - 没值时：opt.has_value() == false
     * 比返回 bool + 引用参数更优雅
     */
    std::optional<Fill> execute_order(const Order& order,
                                       double current_price,
                                       const std::string& date);

    // ── 状态更新 ──

    /*
     * 更新所有持仓的当前价格
     * 在每天结束时调用，用最新收盘价更新市值
     */
    void update_price(const std::string& symbol, double price);

    /*
     * 记录今天的净值快照
     * 每天调用一次，记录到 equity_curve
     */
    void record_equity(const std::string& date);

    // ── 查询方法（const = 不修改状态） ──

    double get_cash() const { return cash_; }
    double get_initial_capital() const { return initial_capital_; }
    double get_market_value() const;      // 所有持仓的总市值
    double get_total_value() const;       // 现金 + 市值

    /*
     * 获取某只股票的持仓
     * 返回 const 指针：nullptr 表示没有持仓
     *
     * 为什么返回 const 指针而不是 optional？
     * 因为 Position 可能比较大，用指针避免拷贝。
     * const 确保调用者不能通过这个指针修改持仓。
     */
    const Position* get_position(const std::string& symbol) const;
    bool has_position(const std::string& symbol) const;
    int get_position_quantity(const std::string& symbol) const;
    double get_position_avg_price(const std::string& symbol) const;

    // ── 获取历史数据 ──

    const std::vector<Fill>& get_fills() const { return fills_; }
    const std::vector<EquitySnapshot>& get_equity_curve() const { return equity_curve_; }
    const std::map<std::string, Position>& get_positions() const { return positions_; }

    // ── 统计 ──
    double get_total_commission() const { return total_commission_; }
    double get_total_slippage() const { return total_slippage_; }

    /* Set the reason on the most recent fill (for risk manager tagging). */
    void set_last_fill_reason(const std::string& reason) {
        if (!fills_.empty()) {
            fills_.back().reason = reason;
        }
    }
    int get_total_trades() const { return static_cast<int>(fills_.size()); }

private:
    double initial_capital_;                        // 初始资金
    double cash_;                                   // 当前现金
    CommissionConfig commission_config_;             // 手续费配置

    /*
     * positions_：持仓表
     * key = 股票代码, value = Position
     * std::map 保证按股票代码字母序排列
     */
    std::map<std::string, Position> positions_;

    std::vector<Fill> fills_;                       // 所有成交记录
    std::vector<EquitySnapshot> equity_curve_;       // 每日净值曲线
    double total_commission_ = 0.0;                 // 累计手续费
    double total_slippage_ = 0.0;                   // 累计滑点成本

    double prev_total_value_ = 0.0;                 // 前一天的总资产（用于计算日收益率）
};

}  // namespace backtest
