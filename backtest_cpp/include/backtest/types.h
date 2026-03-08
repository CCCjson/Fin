/*
 * types.h — 回测系统的基础类型定义
 *
 * 这个文件定义了回测系统中所有核心数据结构：
 * - Bar：一根 K 线（一天的行情数据）
 * - Order：交易订单（买入/卖出指令）
 * - Fill：成交记录（订单执行后的结果）
 * - Position：持仓信息（当前持有的股票）
 *
 * 知识点：
 * - struct vs class：struct 默认 public，class 默认 private
 *   我们用 struct 来定义纯数据结构（没有复杂逻辑的）
 * - enum class：强类型枚举，比普通 enum 更安全
 *   普通 enum 的值会隐式转换为 int，enum class 不会
 */

#pragma once     // 防止头文件被重复包含（编译器指令）

#include <string>
#include <vector>
#include <cmath>       // std::abs

/*
 * 所有代码放在 backtest 命名空间里，避免和其他库的名字冲突。
 * 使用时要写 backtest::Bar 或者 using namespace backtest;
 */
namespace backtest {

// ── 枚举类型 ──

/*
 * Side：交易方向
 * enum class 是 C++11 引入的"强类型枚举"。
 * 和普通 enum 的区别：
 * - 必须写 Side::BUY，不能直接写 BUY
 * - 不会自动转换为 int
 */
enum class Side {
    BUY,    // 买入
    SELL    // 卖出
};

/*
 * OrderType：订单类型
 * MARKET = 市价单，以当前价格立即成交
 * LIMIT  = 限价单，只有达到指定价格才成交
 */
enum class OrderType {
    MARKET,
    LIMIT
};

// ── 辅助函数 ──

/*
 * inline：建议编译器把函数体直接嵌入调用处，减少函数调用开销。
 * 对于这种很短的函数，inline 可以提升性能。
 * 在头文件中定义的函数通常要加 inline，否则多个 .cpp 文件
 * #include 这个头文件时会报"重复定义"错误。
 */
inline std::string side_to_string(Side s) {
    return s == Side::BUY ? "BUY" : "SELL";
}

inline Side string_to_side(const std::string& s) {
    return s == "BUY" ? Side::BUY : Side::SELL;
}

// ── 核心数据结构 ──

/*
 * Bar — 一根 K 线
 *
 * K 线是金融数据的基本单位，记录一个时间段内的价格走势。
 * 日线：一天一根 K 线
 * 包含：开盘价(open)、最高价(high)、最低价(low)、收盘价(close)、成交量(volume)
 *
 * 为什么叫 "Bar"？因为 K 线看起来像一根竖条(bar)。
 */
struct Bar {
    std::string date;       // 日期，如 "2025-01-15"
    double open   = 0.0;    // 开盘价：当天第一笔成交价
    double high   = 0.0;    // 最高价：当天的最高成交价
    double low    = 0.0;    // 最低价：当天的最低成交价
    double close  = 0.0;    // 收盘价：当天最后一笔成交价（最重要的价格）
    double volume = 0.0;    // 成交量：当天成交的股数
};

/*
 * Order — 交易订单
 *
 * 交易者发出的买入/卖出指令。
 * order_id 用来唯一标识这个订单。
 * quantity 用正数表示，side 区分买卖方向。
 */
struct Order {
    std::string order_id;       // 订单编号
    std::string symbol;         // 股票代码，如 "AAPL"
    Side side = Side::BUY;      // 方向：买入 or 卖出
    OrderType type = OrderType::MARKET;  // 类型：市价 or 限价
    int quantity = 0;           // 数量（股数）
    double price = 0.0;         // 限价单的目标价格（市价单为 0）

    /*
     * 工厂方法（Factory Method）：
     * 提供静态方法来创建常用的订单类型，
     * 比手动设置每个字段更方便、更不容易出错。
     *
     * static：静态方法属于类而不是某个对象，
     * 可以不创建对象就直接调用：Order::market_buy("AAPL", 100)
     */
    static Order market_buy(const std::string& sym, int qty) {
        Order o;
        o.symbol = sym;
        o.side = Side::BUY;
        o.type = OrderType::MARKET;
        o.quantity = qty;
        return o;
    }

    static Order market_sell(const std::string& sym, int qty) {
        Order o;
        o.symbol = sym;
        o.side = Side::SELL;
        o.type = OrderType::MARKET;
        o.quantity = qty;
        return o;
    }

    static Order limit_buy(const std::string& sym, int qty, double p) {
        Order o;
        o.symbol = sym;
        o.side = Side::BUY;
        o.type = OrderType::LIMIT;
        o.quantity = qty;
        o.price = p;
        return o;
    }

    static Order limit_sell(const std::string& sym, int qty, double p) {
        Order o;
        o.symbol = sym;
        o.side = Side::SELL;
        o.type = OrderType::LIMIT;
        o.quantity = qty;
        o.price = p;
        return o;
    }
};

/*
 * Fill — 成交记录
 *
 * 当订单被执行（成交）后，生成一条 Fill 记录。
 * 包含实际成交价格、数量、手续费等。
 */
struct Fill {
    std::string order_id;       // 对应的订单编号
    std::string symbol;         // 股票代码
    Side side = Side::BUY;      // 方向
    double price = 0.0;         // 实际成交价格
    int quantity = 0;           // 成交数量
    double commission = 0.0;    // 手续费
    double slippage = 0.0;      // 滑点成本
    std::string date;           // 成交日期
    std::string reason;         // 成交原因: "signal" / "stop_loss" / "trailing_stop"

    /*
     * 计算成交金额（不含手续费）
     */
    double value() const {
        return price * quantity;
    }

    /*
     * 计算含手续费的总成本
     */
    double total_cost() const {
        return value() + commission;
    }
};

/*
 * Position — 持仓
 *
 * 记录当前持有某只股票的状态。
 * 包含数量、平均成本、当前价格。
 * 可以计算未实现盈亏（还没卖出时的浮动盈亏）。
 */
struct Position {
    std::string symbol;             // 股票代码
    int quantity = 0;               // 持有数量
    double avg_cost = 0.0;          // 平均成本价
    double current_price = 0.0;     // 当前市场价格

    /*
     * 市值 = 持有数量 × 当前价格
     * const 方法：承诺不修改对象的任何成员变量
     */
    double market_value() const {
        return quantity * current_price;
    }

    /*
     * 成本 = 持有数量 × 平均成本
     */
    double cost_basis() const {
        return quantity * avg_cost;
    }

    /*
     * 未实现盈亏 = 市值 - 成本
     * > 0 表示盈利，< 0 表示亏损
     */
    double unrealized_pnl() const {
        return market_value() - cost_basis();
    }

    /*
     * 未实现盈亏百分比
     * 如果成本为 0 则返回 0，避免除以零
     */
    double unrealized_pnl_pct() const {
        double cost = cost_basis();
        if (cost == 0.0) return 0.0;
        return (unrealized_pnl() / cost) * 100.0;
    }
};

/*
 * EquitySnapshot — 每日净值快照
 *
 * 回测过程中每天记录一次，用于绘制"资金曲线"。
 * 资金曲线是评价策略好坏最直观的图表。
 */
struct EquitySnapshot {
    std::string date;           // 日期
    double cash = 0.0;          // 现金
    double market_value = 0.0;  // 持仓市值
    double total_value = 0.0;   // 总资产 = 现金 + 市值
    double daily_return = 0.0;  // 当日收益率
};

/*
 * CommissionConfig — 手续费配置
 *
 * 不同市场的手续费规则不同：
 * - A 股：佣金万 2.5 + 印花税千 1（仅卖出）
 * - 美股：佣金 $0.005/股
 * - 港股：佣金万 5 + 印花税千 1
 */
struct CommissionConfig {
    double rate = 0.00025;          // 佣金率（默认万 2.5）
    double stamp_tax = 0.001;       // 印花税率（默认千 1）
    bool stamp_tax_sell_only = true; // 印花税是否只在卖出时收
    double min_commission = 5.0;    // 最低佣金（元/美元）
    double slippage_pct = 0.0;      // 滑点比例（如 0.001 = 0.1%）

    /*
     * 计算某笔交易的总手续费
     * amount = 成交金额（价格 × 数量）
     * is_sell = 是否为卖出
     */
    double calculate(double amount, bool is_sell) const {
        // 佣金 = 成交金额 × 佣金率，不低于最低佣金
        double comm = std::max(amount * rate, min_commission);

        // 印花税：如果只对卖出收，则买入时不收
        if (is_sell || !stamp_tax_sell_only) {
            comm += amount * stamp_tax;
        }

        return comm;
    }

    /*
     * 计算滑点后的实际成交价
     * 买入价格偏高，卖出价格偏低
     */
    double apply_slippage(double price, bool is_buy) const {
        if (slippage_pct <= 0.0) return price;
        if (is_buy) return price * (1.0 + slippage_pct);
        else        return price * (1.0 - slippage_pct);
    }

    /*
     * 预设配置的工厂方法（含默认滑点）
     */
    static CommissionConfig a_share() {
        return { 0.00025, 0.001, true, 5.0, 0.001 };    // 滑点 0.1%
    }

    static CommissionConfig us_stock() {
        return { 0.0001, 0.0, false, 1.0, 0.0005 };     // 滑点 0.05%
    }

    static CommissionConfig hk_stock() {
        return { 0.0005, 0.001, false, 5.0, 0.001 };    // 滑点 0.1%
    }
};

/*
 * RiskConfig — 风控配置
 *
 * 在引擎层面强制执行止损和仓位限制，策略不可绕过。
 */
struct RiskConfig {
    bool   enabled = false;             // 是否启用风控
    double stop_loss_pct = 0.05;        // 固定止损比例（亏损 5% 平仓）
    bool   trailing_stop = false;       // 是否启用追踪止损
    double trailing_stop_pct = 0.08;    // 追踪止损：从最高点回撤 8% 平仓
    double max_position_pct = 1.0;      // 单股最大仓位占总资产比例（1.0 = 不限制）
};

}  // namespace backtest
