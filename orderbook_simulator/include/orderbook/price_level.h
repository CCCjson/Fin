/*
 * ============================================================
 * price_level.h — 单价位 FIFO 订单队列
 * ============================================================
 *
 * 什么是 PriceLevel？
 *
 * 在订单簿里，同一个价格上可能有很多人挂单。
 * 比如 100.00 元这个价格上：
 *   张三挂了 100 股（先来的）
 *   李四挂了 200 股（后来的）
 *   王五挂了  50 股（最后的）
 *
 * PriceLevel 就是管理"同一个价格上的所有订单"的容器。
 * 它按照 FIFO（先进先出 / 先来后到）的顺序处理订单：
 * 有人来买时，先吃张三的，再吃李四的，最后吃王五的。
 *
 * 数据结构选择：std::deque（双端队列）
 *   - 头部弹出 O(1) — 撮合时从前面吃
 *   - 尾部插入 O(1) — 新订单从后面排队
 *   - 比 std::list 内存更紧凑（缓存友好）
 *   - 比 std::vector 头部弹出更快（vector 头部弹出要移动所有元素）
 *
 * ============================================================
 */

#ifndef ORDERBOOK_PRICE_LEVEL_H
#define ORDERBOOK_PRICE_LEVEL_H

#include <deque>       // 双端队列
#include <vector>
#include <string>
#include "orderbook/types.h"

namespace orderbook {

class PriceLevel {
public:
    // ── 构造函数 ──
    // explicit 关键字：防止编译器做隐式类型转换
    // 没有 explicit 的话，PriceLevel level = 100.0; 这种写法会编译通过
    // 加了 explicit 后，必须写 PriceLevel level(100.0); 更安全
    explicit PriceLevel(double price);

    // ── 查询方法（都是 const，不修改对象）──

    /// 这个价位是多少钱
    double price() const;

    /// 该价位上所有活跃订单的总量
    int total_quantity() const;

    /// 该价位上有多少个活跃订单
    int order_count() const;

    /// 是否没有活跃订单了
    bool is_empty() const;

    /// 获取所有活跃订单的列表（返回拷贝，不影响内部状态）
    std::vector<BookOrder> get_orders() const;

    // ── 修改方法 ──

    /// 添加一个新订单到队尾（排队）
    void add_order(BookOrder order);
    // 注意这里参数不是引用（BookOrder order 而不是 BookOrder& order）
    // 这是因为我们要把订单"移入"队列，传值后可以 std::move

    /// 撤销指定订单，返回是否撤销成功
    bool remove_order(const std::string& order_id);

    /// 撮合：用来单的数量去吃这个价位上的订单
    /// 参数：
    ///   incoming_qty      — 来单想成交的数量
    ///   aggressor_side    — 来单的方向（谁主动发起的交易）
    ///   aggressor_order_id — 来单的订单 ID
    /// 返回值：
    ///   pair.first  — 实际成交了多少股
    ///   pair.second — 成交记录列表
    std::pair<int, std::vector<Fill>> match(
        int incoming_qty,
        Side aggressor_side,
        const std::string& aggressor_order_id
    );

private:
    double price_;                     // 这个价位的价格
    std::deque<BookOrder> orders_;     // 订单队列（FIFO）

    // 命名约定：成员变量以下划线结尾（price_、orders_）
    // 这是 Google C++ 代码风格的惯例，方便区分成员变量和局部变量
};

}  // namespace orderbook

#endif // ORDERBOOK_PRICE_LEVEL_H
