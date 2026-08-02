/*
 * pairs_strategy.h — 配对交易（两条腿轮动，long-only）
 *
 * ══════════════════════════════════════════════════════════════════════
 * 【口径声明：这不是教科书版的统计套利，别拿它当市场中性策略用】
 * ══════════════════════════════════════════════════════════════════════
 *
 * 教科书版配对交易是「做多价差 = 买 A + **卖空** B」，两条腿方向相反、
 * 市值相抵，组合对大盘涨跌几乎免疫（市场中性）。
 *
 * 但**本引擎不支持做空** —— `Portfolio::execute_order` 的卖出分支硬性要求
 * `positions_[symbol].available >= quantity`，没有持仓就卖不出去。
 * ⛔ 我们**不**用「允许负持仓」去假装做空：那会让融券成本、保证金、强平
 *    全部凭空消失，回测数字彻底失真 —— 一个漂亮但假的夏普比率，
 *    比没有这个策略更危险。
 *
 * 所以这里实现的是 **long-only 的两条腿轮动**（relative-strength rotation）：
 *
 *     价差 spread = log(P_A) - log(P_B)，z = (spread - 均值) / 标准差
 *
 *     z < -entry_z  →  A 相对 B **便宜**  →  **满仓持有 A**（有 B 就先卖掉）
 *     z > +entry_z  →  A 相对 B **贵**    →  **满仓持有 B**（有 A 就先卖掉）
 *     |z| < exit_z  →  价差回归均值        →  **两腿都清仓**，拿现金
 *     其余区间      →  维持现状（不来回折腾，省手续费）
 *
 * 【与教科书版的差别，用之前必须知道】
 *   1. **不是市场中性**：任何时刻要么持有 A 要么持有 B，完整承担该标的的
 *      beta。大盘整体下跌时它照样亏，教科书版不会。
 *   2. **只赚到价差收敛的一半**：做多价差 = 买 A 卖空 B，A 涨或 B 跌都赚；
 *      这里只持有 A，B 跌了跟我们无关。
 *   3. **换腿有摩擦**：一次轮动 = 一卖一买两笔手续费 + 两次滑点，
 *      比教科书版把腿一直挂着的成本高。
 *   4. z 的统计意义没变（仍是价差的均值回归），变的只是**怎么下注**。
 *
 * ══════════════════════════════════════════════════════════════════════
 * 【数据从哪来：走引擎的 load_data，不再塞进构造函数】
 * ══════════════════════════════════════════════════════════════════════
 *
 * 旧版注释写着「不修改 BacktestEngine，策略构造时接收第二只股票的全部 bars」——
 * 那是单票引擎时代的绕道。它有两个后果：
 *   ① 第二条腿的 bars 只被拿来算 z-score，**从来没真的下过单**
 *      （旧引擎还有一句无条件的 `order.symbol = symbol_`，会把策略指定的
 *      第二条腿改回第一条腿）；
 *   ② 用 `ctx.bar_index` 去索引 `bars2_` —— 而多标的下 bar_index 是
 *      「**每个标的自己的**第几根 bar」，两条腿的交易日历一旦不齐就**错位**，
 *      z 会由不同日子的两个价格算出来，且**不报错**。
 *
 * 现在（S8 批次1 之后）引擎按**日期**推进、支持多标的、共享一份资金，
 * 所以两条腿都由 `engine.load_data(symbol, bars)` 喂进来，策略靠
 * `ctx.symbol` + `ctx.current_bar.date` 自己按**日期**配对 —— ⛔ 不再用下标。
 *
 * 【每天怎么决策】
 * 引擎会在同一天分别为两条腿各调一次 `on_bar`（顺序由 symbol 字典序决定）。
 * 本策略把每次调用的收盘价与持仓量记进当日缓存，**等两条腿都到齐了才决策**，
 * 决策发生在当天**第二次**被调用时（无论那次是 A 还是 B）。
 * 因此：某条腿当天没有 bar（停牌 / 上市晚 / 币 7×24 与股票日历不齐）→
 * 当天**不决策**，也不会拿旧价格凑合算一个 z 出来。
 */

#pragma once

#include "backtest/strategy_base.h"
#include <cmath>
#include <string>
#include <vector>

namespace backtest {

class PairsStrategy : public IStrategy {
public:
    /*
     * symbol1 / symbol2 —— 两条腿的代码，**都必须由调用方显式给出**。
     * 引擎里可能同时 load 了别的标的（组合回测），策略只认这两个，
     * 其余 symbol 的 on_bar 一律返回空单。
     *
     * ⚠️ 构造函数**不再接收 bars**：两条腿的行情都走 engine.load_data()。
     */
    PairsStrategy(const std::string& symbol1 = "",
                  const std::string& symbol2 = "",
                  int lookback = 60,
                  double entry_z = 2.0,
                  double exit_z = 0.5,
                  double position_pct = 0.95);

    std::string name() const override;
    std::string description() const override;
    std::map<std::string, std::string> param_schema() const override;
    void on_init() override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    std::string symbol1_;
    std::string symbol2_;
    int lookback_;
    double entry_z_;
    double exit_z_;
    double position_pct_;

    /*
     * ── 当日缓存 ──
     * 引擎同一天会为两条腿各调一次 on_bar。这里攒齐两条腿的
     * 收盘价与持仓量，攒齐了才决策；日期一变就清空。
     *
     * ⚠️ 这是**有状态**策略。`engine.cpp` 里写着「现有 9 个策略都是从
     *    ctx.history 现算的，故安全；新增有状态策略时要留意」—— 这就是那个
     *    有状态的。它安全的原因是：状态是**按日期分桶**的当日缓存，
     *    而不是跨标的串味的指标前值。
     */
    std::string cur_date_;
    bool   have1_ = false;
    bool   have2_ = false;
    double px1_ = 0.0;
    double px2_ = 0.0;
    int    qty1_ = 0;          // 腿1的持仓（取自 ctx，不是策略自己记账）
    int    qty2_ = 0;
    bool   decided_ = false;   // 今天已经决策过（防同日重复下单）

    /*
     * 价差序列。⭐ **只收录两条腿当天都有收盘价的日子** ——
     * 这就是「按日期配对」的落点：缺 bar 的日子根本不进序列，
     * 于是窗口里每一个价差都由真正同一天的两个价格算出来。
     */
    std::vector<double> spreads_;
};

}  // namespace backtest
