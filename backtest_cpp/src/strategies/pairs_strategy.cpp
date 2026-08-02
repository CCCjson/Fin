/*
 * pairs_strategy.cpp — 配对交易（两条腿轮动）实现
 *
 * 口径、为什么不做空、与教科书版的差别，全部写在 pairs_strategy.h 顶部。
 * 这里只讲实现上两个容易改错的点：
 *
 *   🔴 ① **按日期配对，不按下标**。缺 bar 的日子不进价差序列。
 *   🔴 ② **决策发生在当天第二条腿被调用时**。引擎按 symbol 字典序逐个调
 *        on_bar，只有第二次调用时两条腿的今日价格才都在手上。
 */

#include "pairs_strategy.h"
#include <numeric>

namespace backtest {

PairsStrategy::PairsStrategy(const std::string& symbol1,
                             const std::string& symbol2,
                             int lookback, double entry_z,
                             double exit_z, double position_pct)
    : symbol1_(symbol1)
    , symbol2_(symbol2)
    , lookback_(lookback)
    , entry_z_(entry_z)
    , exit_z_(exit_z)
    , position_pct_(position_pct)
{
}

std::string PairsStrategy::name() const {
    return "PAIRS";
}

std::string PairsStrategy::description() const {
    return "配对交易（两条腿轮动，long-only）：按两只标的价差的 z-score "
           "在「持有便宜的那条腿 / 持有另一条腿 / 空仓」之间切换。"
           "⚠️ 引擎不支持做空，故非市场中性，与教科书版统计套利有差别。";
}

std::map<std::string, std::string> PairsStrategy::param_schema() const {
    return {
        {"symbol2", "配对（第二条腿）标的代码；它的 K 线由 params.bars2 提供"},
        {"lookback", "价差 z-score 的回看周期, 默认60"},
        {"entry_z", "入场z-score阈值, 默认2.0（|z| 超过它就满仓便宜的那条腿）"},
        {"exit_z", "出场z-score阈值, 默认0.5（|z| 小于它就两腿都清仓）"},
        {"position_pct", "仓位比例, 默认0.95"}
    };
}

void PairsStrategy::on_init() {
    // 同一个策略对象被重复 run() 时不能带着上一轮的价差序列
    cur_date_.clear();
    have1_ = have2_ = false;
    px1_ = px2_ = 0.0;
    qty1_ = qty2_ = 0;
    decided_ = false;
    spreads_.clear();
}

std::vector<Order> PairsStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    // 两条腿都得有名字，且不能是同一个标的（同一标的的价差恒为 0，没有意义）
    if (symbol1_.empty() || symbol2_.empty() || symbol1_ == symbol2_) {
        return orders;
    }

    const std::string& today = ctx.current_bar.date;
    if (today != cur_date_) {
        // 换日期 → 清空当日缓存
        cur_date_ = today;
        have1_ = have2_ = false;
        decided_ = false;
    }

    // 记下这条腿今天的收盘价与持仓量
    if (ctx.symbol == symbol1_) {
        px1_ = ctx.current_bar.close;
        qty1_ = ctx.position_quantity;
        have1_ = true;
    } else if (ctx.symbol == symbol2_) {
        px2_ = ctx.current_bar.close;
        qty2_ = ctx.position_quantity;
        have2_ = true;
    } else {
        return orders;   // 组合回测里可能还 load 了别的标的，本策略不管
    }

    /*
     * 🔴 今天只到齐一条腿 → **不决策**。
     * 这不是「等一等」而是「这一天没有可比价差」：另一条腿今天没有 bar
     * （停牌 / 尚未上市 / 交易日历不齐），拿它昨天的价格去和今天的价格
     * 算 z-score，算出来的东西没有意义，还会静默污染整条价差序列。
     */
    if (!have1_ || !have2_) return orders;
    if (decided_) return orders;                         // 同一天只决策一次
    if (!(px1_ > 0.0) || !(px2_ > 0.0)) return orders;   // 价格非法
    decided_ = true;

    // ── 价差序列：只在两条腿都有今日价时追加一条 ──
    spreads_.push_back(std::log(px1_) - std::log(px2_));
    if (static_cast<int>(spreads_.size()) < lookback_) {
        return orders;   // 窗口还没攒满
    }

    // ── 滚动窗口的均值 / 标准差 / z ──
    const size_t n = spreads_.size();
    const auto win_begin =
        spreads_.begin() + static_cast<std::ptrdiff_t>(n - static_cast<size_t>(lookback_));
    double mean = std::accumulate(win_begin, spreads_.end(), 0.0) / lookback_;
    double sq_sum = 0.0;
    for (auto it = win_begin; it != spreads_.end(); ++it) {
        sq_sum += (*it - mean) * (*it - mean);
    }
    double stddev = std::sqrt(sq_sum / lookback_);
    if (stddev < 1e-10) return orders;   // 价差没有波动，z 没有定义

    double z_score = (spreads_.back() - mean) / stddev;

    /*
     * ── 目标腿 ──
     *   0 = 空仓（两条腿都不持有）
     *   1 = 持有腿1（symbol1_）
     *   2 = 持有腿2（symbol2_）
     */
    int holding = (qty1_ > 0) ? 1 : ((qty2_ > 0) ? 2 : 0);
    int target;
    if (z_score < -entry_z_) {
        target = 1;                       // A 相对便宜 → 持有 A
    } else if (z_score > entry_z_) {
        target = 2;                       // A 相对贵   → 换成持有 B
    } else if (std::abs(z_score) < exit_z_) {
        target = 0;                       // 价差回归   → 清仓拿现金
    } else {
        target = holding;                 // 缓冲区：维持现状，省来回折腾的成本
    }

    /*
     * ── 先卖后买 ──
     * 引擎第 (2) 步就是「先卖后买」，同一天卖出释放的现金买单当天就能用上，
     * 所以换腿可以一次性把两张单都挂出去。
     */
    double proceeds = 0.0;   // 卖出腿的预估回款（按今日收盘价估）
    if (target != 1 && qty1_ > 0) {
        orders.push_back(Order::market_sell(symbol1_, qty1_));
        proceeds += qty1_ * px1_;
    }
    if (target != 2 && qty2_ > 0) {
        orders.push_back(Order::market_sell(symbol2_, qty2_));
        proceeds += qty2_ * px2_;
    }

    if (target != 0) {
        const std::string& buy_sym = (target == 1) ? symbol1_ : symbol2_;
        double buy_px = (target == 1) ? px1_ : px2_;
        int held = (target == 1) ? qty1_ : qty2_;
        if (held == 0) {
            /*
             * 🔴 换腿时**必须把卖出腿的回款算进可用资金**。
             * `ctx.cash` 是此刻的共享现金余额 —— 而卖单要到明天开盘才成交，
             * 所以现在的 cash 里还没有那笔钱。只按 ctx.cash 定量的话，
             * 满仓状态下换腿会算出「几乎没钱」→ 新腿只买到零星几股，
             * 组合凭空缩水，而且**不报错**。
             *
             * ⚠️ 这是**估算**（回款按今日收盘价、买入也按今日收盘价，实际两笔
             *    都在明日开盘成交），可能略微高估。高估的后果是引擎的资金竞争
             *    逻辑按比例削一刀，安全；position_pct 默认 0.95 也留了缓冲。
             * ⛔ 一手股数一律走 ctx.lot_floor()，别手写 `/100)*100` —— 那是
             *    A 股假设，对 crypto 直接失效。
             */
            double available = (ctx.cash + proceeds) * position_pct_;
            int qty = ctx.lot_floor(available / buy_px);
            if (qty > 0) {
                orders.push_back(Order::market_buy(buy_sym, qty));
            }
        }
    }

    return orders;
}

}  // namespace backtest
