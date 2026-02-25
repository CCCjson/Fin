/*
 * price_level.cpp — PriceLevel 的具体实现
 *
 * .h 文件声明了"有哪些函数"，.cpp 文件写"函数具体怎么干活"。
 * 这种分离是 C++ 的传统做法：
 *   - 头文件(.h)给别人看接口（"我能做什么"）
 *   - 源文件(.cpp)写实现（"我怎么做"）
 */

#include "orderbook/price_level.h"

// 使用 orderbook 命名空间，这样就不用每次都写 orderbook::xxx
namespace orderbook {

// ============================================================
// 构造函数
// ============================================================

PriceLevel::PriceLevel(double price)
    : price_(price)    // 初始化列表：直接初始化成员变量
                       // 比在函数体内赋值更高效（跳过了默认构造+赋值的两步）
{
    // orders_ 自动初始化为空的 deque，不需要手动处理
}


// ============================================================
// 查询方法
// ============================================================

double PriceLevel::price() const {
    return price_;
}

int PriceLevel::total_quantity() const {
    int total = 0;
    // 范围 for 循环（C++11 引入），遍历 orders_ 里的每个元素
    // const auto& 的含义：
    //   auto  = 让编译器自动推断类型（这里是 BookOrder）
    //   &     = 引用，不拷贝（高效）
    //   const = 不修改（只读）
    for (const auto& order : orders_) {
        if (order.is_active) {
            total += order.remaining();
        }
    }
    return total;
}

int PriceLevel::order_count() const {
    int count = 0;
    for (const auto& order : orders_) {
        if (order.is_active) {
            count++;
        }
    }
    return count;
}

bool PriceLevel::is_empty() const {
    // 如果没有任何活跃订单，就认为是空的
    // 队列里可能有已取消/已成交的"尸体"，但它们不算
    for (const auto& order : orders_) {
        if (order.is_active) return false;
    }
    return true;
}

std::vector<BookOrder> PriceLevel::get_orders() const {
    std::vector<BookOrder> result;
    for (const auto& order : orders_) {
        if (order.is_active) {
            result.push_back(order);   // push_back = 在 vector 尾部追加一个元素
        }
    }
    return result;
}


// ============================================================
// 修改方法
// ============================================================

void PriceLevel::add_order(BookOrder order) {
    // std::move 把 order 的内容"转移"给 deque，而不是拷贝
    // 转移后 order 变成"空壳"，但我们不再需要它了
    // 这避免了一次不必要的深拷贝（特别是 string 类型的拷贝开销）
    orders_.push_back(std::move(order));
}

bool PriceLevel::remove_order(const std::string& order_id) {
    // 遍历所有订单，找到匹配的活跃订单并取消它
    for (auto& order : orders_) {
        // 注意这里用 auto&（没有 const），因为我们要修改 order
        if (order.order_id == order_id && order.is_active) {
            order.cancel();
            return true;    // 找到了，撤销成功
        }
    }
    return false;   // 没找到这个订单
}

std::pair<int, std::vector<Fill>> PriceLevel::match(
    int incoming_qty,
    Side aggressor_side,
    const std::string& aggressor_order_id
) {
    // std::pair 是"一对值"的容器，相当于 Python 的 tuple(a, b)
    // 这里返回 (实际成交量, 成交记录列表)

    std::vector<Fill> fills;
    int matched = 0;     // 已经成交了多少

    // 从队列头部开始，逐个吃订单
    // 为什么从头部？因为头部是最早挂上去的订单（时间优先）
    while (!orders_.empty() && matched < incoming_qty) {
        // front() 获取队列最前面的元素（引用，不拷贝）
        BookOrder& resting = orders_.front();

        // 跳过已经不活跃的订单（已撤单或已成交的"尸体"）
        if (!resting.is_active) {
            orders_.pop_front();   // pop_front = 弹出队列最前面的元素
            continue;
        }

        // 计算这一笔能成交多少
        // std::min 取较小值：要么吃完这个挂单，要么来单的剩余量先用完
        int trade_qty = std::min(resting.remaining(), incoming_qty - matched);

        // 让挂单记录成交
        resting.fill(trade_qty);
        matched += trade_qty;

        // 创建成交记录
        Fill fill;
        fill.fill_id = generate_id();
        fill.price = price_;       // 成交价 = 挂单的价格（价格优先原则）
        fill.quantity = trade_qty;
        fill.aggressor_side = aggressor_side;
        fill.timestamp = now_ns();

        // 根据主动方方向，确定买方和卖方的 order_id
        if (aggressor_side == Side::BUY) {
            fill.buy_order_id = aggressor_order_id;   // 来单是买方（主动吃卖盘）
            fill.sell_order_id = resting.order_id;     // 挂单是卖方
        } else {
            fill.sell_order_id = aggressor_order_id;   // 来单是卖方（主动吃买盘）
            fill.buy_order_id = resting.order_id;      // 挂单是买方
        }

        fills.push_back(std::move(fill));

        // 如果这个挂单已经全部成交了，从队列中移除
        if (resting.is_filled()) {
            orders_.pop_front();
        }
    }

    return {matched, std::move(fills)};
    // 返回时用 std::move(fills) 避免拷贝整个 vector
}

}  // namespace orderbook
