/*
 * external_signal_strategy.cpp — 外部信号回放策略的实现
 */

#include "external_signal_strategy.h"
#include <cmath>       // std::floor

namespace backtest {

ExternalSignalStrategy::ExternalSignalStrategy(std::map<std::string, SignalEntry> signals)
    : signals_(std::move(signals))
{
}

std::string ExternalSignalStrategy::name() const {
    return "SIGNAL";
}

std::string ExternalSignalStrategy::description() const {
    return "外部信号回放：按传入的 [{date, action, price?, weight?}] 序列在对应日期成交";
}

/*
 * on_bar — 查当天有没有信号，有就翻译成 Order
 *
 * 数量口径与内置策略一致（见 ma_cross_strategy.cpp）：
 * - BUY：可用现金 × weight / 当前收盘价，向下取整到 100 股（A 股一手）；<100 不发单
 *        （天然处理"已满仓/现金不足"——此时算出的 qty < 100）
 * - SELL：全平当前持仓（无持仓则 no-op）
 * 成交由引擎推迟到次日开盘（防未来函数），本函数只产订单不管撮合。
 */
std::vector<Order> ExternalSignalStrategy::on_bar(const StrategyContext& ctx) {
    std::vector<Order> orders;

    auto it = signals_.find(ctx.current_bar.date);
    if (it == signals_.end()) {
        return orders;   // 今天没有信号
    }

    const SignalEntry& sig = it->second;

    if (sig.side == Side::BUY) {
        double close = ctx.current_bar.close;
        if (close <= 0.0) {
            return orders;
        }
        double available = ctx.cash * sig.weight;
        int qty = static_cast<int>(available / close / 100) * 100;
        if (qty >= 100) {
            if (sig.has_price) {
                orders.push_back(Order::limit_buy(ctx.symbol, qty, sig.price));
            } else {
                orders.push_back(Order::market_buy(ctx.symbol, qty));
            }
        }
    } else {  // SELL
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
