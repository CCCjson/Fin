/*
 * portfolio_signal_strategy.cpp — 组合信号回放的实现
 */

#include "portfolio_signal_strategy.h"

namespace backtest {

PortfolioSignalStrategy::PortfolioSignalStrategy(
    std::map<std::string, std::map<std::string, SignalEntry>> by_symbol)
    : by_symbol_(std::move(by_symbol))
{
}

std::string PortfolioSignalStrategy::name() const {
    return "PORTFOLIO_SIGNAL";
}

std::string PortfolioSignalStrategy::description() const {
    return "组合信号回放：每个标的各一条 [{date, action, price?, weight?}] 序列，共享一份资金";
}

std::vector<Order> PortfolioSignalStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    auto sit = by_symbol_.find(ctx.symbol);
    if (sit == by_symbol_.end()) {
        return orders;   // 这个标的没有信号序列（只是被拿来当行情背景）
    }
    auto it = sit->second.find(ctx.current_bar.date);
    if (it == sit->second.end()) {
        return orders;   // 今天这个标的没有信号
    }

    const SignalEntry& sig = it->second;

    if (sig.side == Side::BUY) {
        double close = ctx.current_bar.close;
        if (close <= 0.0) {
            return orders;
        }
        /*
         * weight 是「想动用当前**共享现金**的比例」，不是「组合权重」。
         * 同一天几个标的都说 0.95，请求合计会远超 100% —— 那不是 bug，
         * 引擎会按名义额等比缩减（见 engine.cpp 的资金竞争段）。
         * ⛔ 别在这里先按标的数把 weight 除一遍来「避免超额」：
         *    那等于给每个标的划了一份固定的钱，共享资金池就名存实亡了。
         */
        int qty = ctx.afford(sig.weight, close);
        if (qty > 0) {
            if (sig.has_price) {
                orders.push_back(Order::limit_buy(ctx.symbol, qty, sig.price));
            } else {
                orders.push_back(Order::market_buy(ctx.symbol, qty));
            }
        }
    } else {  // SELL：全平这个标的的持仓
        if (ctx.position_quantity > 0) {
            if (sig.has_price) {
                orders.push_back(Order::limit_sell(ctx.symbol, ctx.position_quantity, sig.price));
            } else {
                orders.push_back(Order::market_sell(ctx.symbol, ctx.position_quantity));
            }
        }
    }

    return orders;
}

}  // namespace backtest
