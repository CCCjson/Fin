/*
 * ============================================================
 * types.h — 订单簿模拟器的基础类型定义
 * ============================================================
 *
 * 这个文件定义了整个项目会用到的基本数据类型：
 *   - Side        : 买还是卖
 *   - OrderType   : 订单类型（市价、限价等）
 *   - BookOrder   : 一个订单的完整信息
 *   - Fill        : 一笔成交记录
 *
 * 为什么放在一个文件里？
 * 因为这些类型到处都会用到，集中定义方便管理，避免循环依赖。
 *
 * ============================================================
 */

#ifndef ORDERBOOK_TYPES_H     // "头文件保护"：防止同一个文件被 #include 两次
#define ORDERBOOK_TYPES_H     // 如果已经定义过这个宏，就跳过整个文件

#include <string>
#include <cstdint>   // int64_t — 64 位整数，用来存时间戳（纳秒级）
#include <chrono>    // C++ 标准库的时间工具

// nlohmann/json 是我们引入的第三方 JSON 库
// 它可以让 C++ 对象方便地转换为 JSON 字符串
#include <nlohmann/json.hpp>

// ============================================================
// 枚举类型 (enum class)
// ============================================================
//
// enum class vs enum:
//   普通 enum：  enum Color { RED, GREEN };  RED 就是全局的 0
//   enum class： enum class Color { RED, GREEN };  必须写 Color::RED
//   enum class 更安全，不会和别的枚举冲突

namespace orderbook {

/// 买卖方向
enum class Side {
    BUY,    // 买入（出价方，bid）
    SELL    // 卖出（要价方，ask）
};

/// 订单类型
enum class OrderType {
    MARKET,  // 市价单：不限价格，无条件吃掉对手盘。适合"我现在就要买/卖"
    LIMIT,   // 限价单：只在指定价格或更优价格成交。没成交的部分挂在订单簿上等着
    IOC,     // Immediate or Cancel：能成多少成多少，剩下的立刻取消（不挂单）
    FOK      // Fill or Kill：要么全部成交，要么一股都不成交（全有或全无）
};


// ============================================================
// 辅助函数：枚举 ↔ 字符串 互转
// ============================================================
// 为什么需要这些？
// 因为 JSON API 传的是字符串 "BUY"，不是枚举值 Side::BUY
// 我们需要互相转换

/// Side 转字符串
inline std::string side_to_string(Side s) {
    // switch 语句：根据 s 的值跳转到对应分支
    switch (s) {
        case Side::BUY:  return "BUY";
        case Side::SELL: return "SELL";
    }
    return "UNKNOWN";
}

/// 字符串转 Side
inline Side string_to_side(const std::string& s) {
    // const std::string& 的含义：
    //   const  = 不会修改这个字符串
    //   &      = 引用传递，不拷贝字符串（节省内存和时间）
    if (s == "BUY")  return Side::BUY;
    if (s == "SELL") return Side::SELL;
    return Side::BUY;  // 默认值
}

/// OrderType 转字符串
inline std::string order_type_to_string(OrderType t) {
    switch (t) {
        case OrderType::MARKET: return "MARKET";
        case OrderType::LIMIT:  return "LIMIT";
        case OrderType::IOC:    return "IOC";
        case OrderType::FOK:    return "FOK";
    }
    return "UNKNOWN";
}

/// 字符串转 OrderType
inline OrderType string_to_order_type(const std::string& s) {
    if (s == "MARKET") return OrderType::MARKET;
    if (s == "LIMIT")  return OrderType::LIMIT;
    if (s == "IOC")    return OrderType::IOC;
    if (s == "FOK")    return OrderType::FOK;
    return OrderType::LIMIT;  // 默认值
}


// ============================================================
// BookOrder — 订单簿中的一个订单
// ============================================================
//
// struct vs class:
//   在 C++ 里，struct 和 class 几乎完全一样
//   唯一区别：struct 的成员默认是 public（公开的）
//             class 的成员默认是 private（私有的）
//   一般习惯：简单的数据容器用 struct，复杂的逻辑用 class

struct BookOrder {
    std::string order_id;       // 订单唯一标识（如 "a3f2b1c8"）
    Side side;                  // 买还是卖
    OrderType order_type;       // 订单类型
    double price;               // 价格（市价单为 0）
    int quantity;               // 总数量（想买/卖多少股）
    int filled_quantity;        // 已成交数量（已经买到/卖出多少股）
    int64_t timestamp;          // 下单时间（纳秒时间戳）
    bool is_active;             // 是否还活跃（没被取消或全部成交）
    std::string client_tag;     // 客户端标签：区分是用户下的单还是系统播种的

    // ── 构造函数 ──
    // 构造函数是创建对象时自动调用的函数
    // 这里用了"默认参数"：如果创建时不传某个参数，就用默认值
    BookOrder()
        : side(Side::BUY)
        , order_type(OrderType::LIMIT)
        , price(0.0)
        , quantity(0)
        , filled_quantity(0)
        , timestamp(0)
        , is_active(true)
    {}

    // ── 成员函数 ──

    /// 剩余未成交数量
    int remaining() const {
        // const 放在函数括号后面，意思是"这个函数不会修改对象的任何成员"
        // 这是一种承诺，编译器会帮你检查
        return quantity - filled_quantity;
    }

    /// 是否已经全部成交
    bool is_filled() const {
        return filled_quantity >= quantity;
    }

    /// 部分成交：增加已成交数量
    void fill(int qty) {
        filled_quantity += qty;
        if (is_filled()) {
            is_active = false;   // 全部成交后自动变为不活跃
        }
    }

    /// 撤单
    void cancel() {
        is_active = false;
    }

    /// 转换为 JSON 对象（给 API 返回用）
    nlohmann::json to_json() const {
        return {
            {"order_id", order_id},
            {"side", side_to_string(side)},
            {"order_type", order_type_to_string(order_type)},
            {"price", price},
            {"quantity", quantity},
            {"filled_quantity", filled_quantity},
            {"remaining", remaining()},
            {"timestamp", timestamp},
            {"is_active", is_active},
            {"client_tag", client_tag}
        };
    }
};


// ============================================================
// Fill — 一笔成交记录
// ============================================================
//
// 每当一个买单和一个卖单"碰上了"，就会产生一笔 Fill。
// 比如：张三挂了 100 股的卖单 @101.00
//       李四下了市价买单 100 股
//       → 产生 Fill: 价格 101.00，数量 100，主动方=买（李四发起的）

struct Fill {
    std::string fill_id;          // 成交唯一标识
    std::string buy_order_id;     // 买方订单 ID
    std::string sell_order_id;    // 卖方订单 ID
    double price;                 // 成交价格
    int quantity;                 // 成交数量
    Side aggressor_side;          // 主动方：是谁发起了这笔交易
    int64_t timestamp;            // 成交时间

    Fill()
        : price(0.0)
        , quantity(0)
        , aggressor_side(Side::BUY)
        , timestamp(0)
    {}

    nlohmann::json to_json() const {
        return {
            {"fill_id", fill_id},
            {"buy_order_id", buy_order_id},
            {"sell_order_id", sell_order_id},
            {"price", price},
            {"quantity", quantity},
            {"aggressor_side", side_to_string(aggressor_side)},
            {"timestamp", timestamp}
        };
    }
};


// ============================================================
// 工具函数
// ============================================================

/// 获取当前时间的纳秒时间戳
inline int64_t now_ns() {
    // std::chrono 是 C++ 的时间库
    // system_clock::now() 获取当前时间
    // time_since_epoch() 获取从 1970-01-01 00:00:00 到现在的时间段
    // .count() 把时间段转成数字
    auto now = std::chrono::system_clock::now();
    auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        now.time_since_epoch()
    );
    return ns.count();
}

/// 生成简单的唯一 ID（基于时间戳 + 计数器）
inline std::string generate_id() {
    // static 变量只初始化一次，之后每次调用都在原来的值上累加
    // 这意味着 counter 的值在整个程序运行期间一直递增
    static int64_t counter = 0;
    counter++;
    auto ts = now_ns();

    // std::to_string 把数字转成字符串
    // substr 截取字符串的一部分（这里取后 8 位，让 ID 短一些）
    std::string ts_str = std::to_string(ts);
    std::string c_str = std::to_string(counter);
    // 取时间戳后 8 位 + 计数器，保证唯一
    return ts_str.substr(ts_str.size() > 8 ? ts_str.size() - 8 : 0) + c_str;
}

}  // namespace orderbook

#endif // ORDERBOOK_TYPES_H
