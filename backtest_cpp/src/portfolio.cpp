/*
 * portfolio.cpp — Portfolio 类的实现
 *
 * 这个文件实现了 portfolio.h 中声明的所有方法。
 * C++ 的惯例是把声明放在 .h 头文件，实现放在 .cpp 源文件。
 *
 * 好处：
 * 1. 修改实现不需要重新编译包含了 .h 的其他文件
 * 2. 隐藏实现细节，只暴露接口
 */

#include "backtest/portfolio.h"
#include <algorithm>   // std::max

namespace backtest {

/*
 * 构造函数实现
 *
 * 初始化列表（: 后面的部分）：
 * 这是 C++ 初始化成员变量的推荐方式，
 * 比在函数体里赋值更高效（直接构造，不是先默认构造再赋值）。
 *
 * std::move(commission)：
 * "移动语义"——把 commission 的内容"搬"进 commission_config_，
 * 而不是"复制"一份。对于简单类型区别不大，
 * 但对于 string/vector 等大对象可以避免不必要的拷贝。
 */
Portfolio::Portfolio(double initial_capital, CommissionConfig commission)
    : initial_capital_(initial_capital)
    , cash_(initial_capital)
    , commission_config_(std::move(commission))
    , prev_total_value_(initial_capital)
{
}

/*
 * execute_order — 执行订单的核心逻辑
 *
 * 流程：
 * 1. 确定成交价格（市价单 = current_price，限价单 = 订单价格）
 * 2. 检查是否能执行（买入：钱够不够？卖出：股票够不够？）
 * 3. 计算手续费
 * 4. 更新现金和持仓
 * 5. 记录成交
 */
std::optional<Fill> Portfolio::execute_order(const Order& order,
                                              double current_price,
                                              const std::string& date) {
    // ── Step 1: 确定成交价 ──
    double fill_price = current_price;
    if (order.type == OrderType::LIMIT) {
        /*
         * 限价单逻辑：
         * - 买入限价单：只有当市场价 ≤ 限价才成交
         *   （你说"最多出100块"，如果市场价105就不买）
         * - 卖出限价单：只有当市场价 ≥ 限价才成交
         *   （你说"最少卖100块"，如果市场价95就不卖）
         */
        if (order.side == Side::BUY && current_price > order.price) {
            return std::nullopt;   // 价格太高，不买
        }
        if (order.side == Side::SELL && current_price < order.price) {
            return std::nullopt;   // 价格太低，不卖
        }
        fill_price = current_price;  // 按市场价成交（更保守的模拟）
    }

    // ── Step 2: 检查是否能执行 ──
    double amount = fill_price * order.quantity;    // 成交金额
    bool is_sell = (order.side == Side::SELL);
    double commission = commission_config_.calculate(amount, is_sell);

    if (order.side == Side::BUY) {
        // 买入检查：现金是否足够支付 金额 + 手续费
        if (cash_ < amount + commission) {
            return std::nullopt;   // 钱不够，订单被拒绝
        }
    } else {
        // 卖出检查：是否持有足够的股票
        auto it = positions_.find(order.symbol);
        if (it == positions_.end() || it->second.quantity < order.quantity) {
            return std::nullopt;   // 股票不够，订单被拒绝
        }
    }

    // ── Step 3: 更新现金 ──
    if (order.side == Side::BUY) {
        cash_ -= (amount + commission);    // 买入：扣钱
    } else {
        cash_ += (amount - commission);    // 卖出：加钱（扣除手续费）
    }
    total_commission_ += commission;

    // ── Step 4: 更新持仓 ──
    if (order.side == Side::BUY) {
        /*
         * 买入：增加持仓
         * 如果已有持仓，需要重新计算平均成本：
         * 新均价 = (旧成本 + 新成本) / 新总量
         *
         * 例如：原来 100 股 × 10 元 = 1000 元成本
         *       新买 50 股 × 12 元 = 600 元成本
         *       新均价 = (1000 + 600) / 150 = 10.67 元
         *
         * [] 运算符：如果 key 不存在，会自动创建一个默认值
         * 这里如果第一次买入，positions_[symbol] 会创建一个空 Position
         */
        auto& pos = positions_[order.symbol];
        pos.symbol = order.symbol;
        double old_cost = pos.avg_cost * pos.quantity;
        double new_cost = fill_price * order.quantity;
        pos.quantity += order.quantity;
        pos.avg_cost = (old_cost + new_cost) / pos.quantity;
        pos.current_price = fill_price;
    } else {
        /*
         * 卖出：减少持仓
         * 如果卖光了，从 positions_ 里删除
         */
        auto& pos = positions_[order.symbol];
        pos.quantity -= order.quantity;
        if (pos.quantity <= 0) {
            positions_.erase(order.symbol);
        }
    }

    // ── Step 5: 记录成交 ──
    Fill fill;
    fill.order_id = order.order_id;
    fill.symbol = order.symbol;
    fill.side = order.side;
    fill.price = fill_price;
    fill.quantity = order.quantity;
    fill.commission = commission;
    fill.date = date;

    fills_.push_back(fill);   // 添加到成交历史

    return fill;   // 返回成交记录
}

/*
 * update_price — 更新某只股票的当前价格
 *
 * 回测中每天收盘后调用，用最新收盘价更新持仓的市值。
 * 这样 get_total_value() 能反映最新的总资产。
 */
void Portfolio::update_price(const std::string& symbol, double price) {
    auto it = positions_.find(symbol);
    if (it != positions_.end()) {
        it->second.current_price = price;
    }
}

/*
 * record_equity — 记录每日净值快照
 *
 * 每天结束时调用，把当天的总资产记录下来。
 * 这些快照连起来就是"资金曲线"（equity curve），
 * 是评价策略表现最直观的图表。
 *
 * daily_return = (今天总资产 - 昨天总资产) / 昨天总资产
 */
void Portfolio::record_equity(const std::string& date) {
    double total = get_total_value();

    EquitySnapshot snap;
    snap.date = date;
    snap.cash = cash_;
    snap.market_value = get_market_value();
    snap.total_value = total;

    // 计算日收益率
    if (prev_total_value_ > 0) {
        snap.daily_return = (total - prev_total_value_) / prev_total_value_;
    }

    equity_curve_.push_back(snap);
    prev_total_value_ = total;
}

/*
 * get_market_value — 所有持仓的总市值
 *
 * 遍历 positions_，累加每个持仓的 market_value()。
 * const auto& [symbol, pos]：C++17 的结构化绑定（structured binding），
 * 把 map 的 key 和 value 分别绑定到 symbol 和 pos。
 * 比写 it->first 和 it->second 更直观。
 */
double Portfolio::get_market_value() const {
    double total = 0.0;
    for (const auto& [symbol, pos] : positions_) {
        total += pos.market_value();
    }
    return total;
}

/*
 * get_total_value — 总资产 = 现金 + 市值
 */
double Portfolio::get_total_value() const {
    return cash_ + get_market_value();
}

/*
 * get_position — 获取某只股票的持仓
 *
 * 返回 const 指针：
 * - 找到了：返回指向 Position 的指针
 * - 没找到：返回 nullptr
 *
 * 调用者需要检查 nullptr：
 *   const Position* p = portfolio.get_position("AAPL");
 *   if (p) { ... 使用 p->quantity 等 ... }
 */
const Position* Portfolio::get_position(const std::string& symbol) const {
    auto it = positions_.find(symbol);
    if (it != positions_.end()) {
        return &(it->second);   // & 取地址，返回指针
    }
    return nullptr;
}

bool Portfolio::has_position(const std::string& symbol) const {
    auto it = positions_.find(symbol);
    return it != positions_.end() && it->second.quantity > 0;
}

int Portfolio::get_position_quantity(const std::string& symbol) const {
    auto it = positions_.find(symbol);
    if (it != positions_.end()) return it->second.quantity;
    return 0;
}

double Portfolio::get_position_avg_price(const std::string& symbol) const {
    auto it = positions_.find(symbol);
    if (it != positions_.end()) return it->second.avg_cost;
    return 0.0;
}

}  // namespace backtest
