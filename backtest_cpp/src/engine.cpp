/*
 * engine.cpp — 回测引擎的实现
 *
 * 这是整个回测系统最核心的文件。
 * run() 方法实现了"逐 bar 回测"的完整循环。
 */

#include "backtest/engine.h"
#include "backtest/strategy_context.h"
#include <algorithm>   // std::find_if
#include <stdexcept>   // std::runtime_error

namespace backtest {

BacktestEngine::BacktestEngine(double initial_capital, CommissionConfig commission, RiskConfig risk_config)
    : initial_capital_(initial_capital)
    , commission_config_(std::move(commission))
    , risk_config_(risk_config)
{
}

/*
 * set_strategy — 接管策略对象的所有权
 *
 * std::move(strategy)：把策略的所有权从调用者转移给 strategy_ 成员。
 * 转移后，原来的 unique_ptr 变成 nullptr。
 * 这是 C++11 "移动语义"的核心应用。
 */
void BacktestEngine::set_strategy(std::unique_ptr<IStrategy> strategy) {
    strategy_ = std::move(strategy);
}

/*
 * load_data — 接收 K 线数据
 *
 * std::move(bars)：把数据"搬"到 bars_ 成员，而不是复制。
 * 这对于几千条数据来说，效率提升很明显。
 */
void BacktestEngine::load_data(const std::string& symbol, std::vector<Bar> bars) {
    symbol_ = symbol;
    bars_ = std::move(bars);
}

/*
 * run — 回测核心循环
 *
 * 这是整个系统最重要的方法。执行流程：
 *
 * 1. 参数验证
 * 2. 创建 Portfolio
 * 3. 日期范围过滤
 * 4. strategy->on_init() — 初始化策略
 * 5. 逐 bar 循环：
 *    a. 更新持仓市值
 *    b. 构建 StrategyContext
 *    c. 调用 strategy->on_bar(ctx) 获取订单
 *    d. 执行订单
 *    e. 记录净值
 * 6. strategy->on_finish() — 清理
 * 7. 计算绩效指标
 * 8. 返回结果
 */
BacktestResult BacktestEngine::run(const std::string& start_date,
                                    const std::string& end_date) {
    // ── Step 1: 参数验证 ──
    if (!strategy_) {
        throw std::runtime_error("No strategy set. Call set_strategy() first.");
    }
    if (bars_.empty()) {
        throw std::runtime_error("No data loaded. Call load_data() first.");
    }

    // ── Step 2: 创建投资组合 ──
    Portfolio portfolio(initial_capital_, commission_config_);

    // ── Step 3: 日期范围过滤 ──
    /*
     * 找到 start_date 和 end_date 对应的索引。
     * 如果没有指定日期，就用全部数据。
     *
     * std::string 的比较运算符可以直接比较日期字符串，
     * 因为 "2025-01-15" > "2025-01-10"（按字典序比较）。
     * 这要求日期格式必须是 YYYY-MM-DD（前面补零）。
     */
    int start_idx = 0;
    int end_idx = static_cast<int>(bars_.size()) - 1;

    if (!start_date.empty()) {
        for (int i = 0; i < static_cast<int>(bars_.size()); ++i) {
            if (bars_[i].date >= start_date) {
                start_idx = i;
                break;
            }
        }
    }
    if (!end_date.empty()) {
        for (int i = static_cast<int>(bars_.size()) - 1; i >= 0; --i) {
            if (bars_[i].date <= end_date) {
                end_idx = i;
                break;
            }
        }
    }

    // ── Step 4: 初始化策略和风控 ──
    strategy_->on_init();
    RiskManager risk_mgr(risk_config_);

    // ── Step 5: 逐 bar 循环 ──
    std::vector<Bar> history;

    for (int i = start_idx; i <= end_idx; ++i) {
        const Bar& bar = bars_[i];

        // (a) 更新持仓的当前价格（用今天的收盘价）
        portfolio.update_price(symbol_, bar.close);

        // (b) 把当前 bar 加入历史
        history.push_back(bar);

        // (c) 构建策略上下文
        StrategyContext ctx;
        ctx.symbol = symbol_;
        ctx.bar_index = i - start_idx;
        ctx.current_bar = bar;
        ctx.history = &history;
        ctx.cash = portfolio.get_cash();
        ctx.position_quantity = portfolio.get_position_quantity(symbol_);
        ctx.position_avg_price = portfolio.get_position_avg_price(symbol_);
        ctx.total_value = portfolio.get_total_value();

        // (d) 风控检查：是否触发止损（在策略决策之前）
        auto risk_orders = risk_mgr.check_stop_loss(
            symbol_, bar.close, ctx.position_quantity,
            ctx.position_avg_price, ctx.total_value);

        if (!risk_orders.empty()) {
            // 强制止损，执行风控订单
            for (auto& order : risk_orders) {
                auto fill = portfolio.execute_order(order, bar.close, bar.date);
                if (fill) {
                    // order_id = "RISK_stop_loss" or "RISK_trailing_stop"
                    std::string reason = order.order_id.length() > 5
                        ? order.order_id.substr(5) : "stop_loss";
                    portfolio.set_last_fill_reason(reason);
                }
            }
            risk_mgr.reset_tracking(symbol_);
            // 止损后跳过策略决策，直接记录净值
            portfolio.record_equity(bar.date);
            continue;
        }

        // (e) 调用策略的 on_bar() 方法
        auto orders = strategy_->on_bar(ctx);

        // (f) 执行所有订单（风控过滤买入量）
        for (auto& order : orders) {
            order.symbol = symbol_;

            // 风控：限制买入仓位
            if (risk_mgr.is_enabled() && order.side == Side::BUY) {
                int adjusted = risk_mgr.filter_buy_quantity(
                    order.quantity, bar.close, portfolio.get_total_value());
                if (adjusted <= 0) continue;  // 拒绝买入
                order.quantity = adjusted;
            }

            auto fill = portfolio.execute_order(order, bar.close, bar.date);

            // 买入后开始追踪止损
            if (fill && order.side == Side::BUY && risk_mgr.is_enabled()) {
                // trailing state 会在 check_stop_loss 中自动初始化
            }
            // 卖出后重置追踪
            if (fill && order.side == Side::SELL) {
                if (!portfolio.has_position(symbol_)) {
                    risk_mgr.reset_tracking(symbol_);
                }
            }
        }

        // (g) 记录今天的净值
        portfolio.record_equity(bar.date);
    }

    // ── Step 6: 策略清理 ──
    strategy_->on_finish();

    // ── Step 7: 计算绩效指标 ──
    auto metrics = Metrics::calculate(
        portfolio.get_equity_curve(),
        portfolio.get_fills(),
        initial_capital_
    );

    // ── Step 8: 组装结果 ──
    BacktestResult result;
    result.symbol = symbol_;
    result.strategy_name = strategy_->name();
    result.metrics = metrics;
    result.equity_curve = portfolio.get_equity_curve();
    result.trades = portfolio.get_fills();

    return result;
}

}  // namespace backtest
