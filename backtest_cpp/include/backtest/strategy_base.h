/*
 * strategy_base.h — 策略抽象基类 (IStrategy)
 *
 * 这是整个回测系统中最重要的 OOP 设计：
 *
 * 【策略模式 (Strategy Pattern)】
 * 策略模式是一种设计模式，核心思想是：
 * - 定义一个通用接口（IStrategy）
 * - 不同的策略各自实现这个接口
 * - 回测引擎只认接口，不关心具体是哪个策略
 *
 * 好处：
 * 1. 添加新策略不需要修改引擎代码（开闭原则）
 * 2. 策略可以随时替换（多态）
 * 3. 策略和引擎解耦，各自独立测试
 *
 * 类比：
 * IStrategy 就像一个"插座"的标准，
 * 不管什么牌子的电器（策略），只要符合这个标准就能用。
 *
 * 知识点：
 * - 纯虚函数 `= 0`：子类必须实现，否则也变成抽象类
 * - 虚析构函数 `virtual ~`：通过基类指针 delete 子类时，
 *   确保子类的析构函数被正确调用，避免内存泄漏
 * - override 关键字：明确告诉编译器"我在重写父类的虚函数"
 *   如果父类没有这个函数，编译器会报错，帮你找 bug
 */

#pragma once

#include "strategy_context.h"
#include "types.h"
#include <vector>
#include <string>
#include <map>

namespace backtest {

/*
 * IStrategy — 策略接口（纯虚基类）
 *
 * "I" 前缀表示 Interface（接口），这是一种命名惯例。
 * 接口 = 只有纯虚函数的类，不能实例化，
 * 只能被继承，子类必须实现所有纯虚函数。
 *
 * 为什么不能实例化？
 * 因为纯虚函数没有函数体（= 0），
 * 如果你 new IStrategy() 然后调用 on_bar()，它不知道该执行什么。
 */
class IStrategy {
public:
    /*
     * 虚析构函数
     *
     * 为什么需要 virtual？
     * 假设：IStrategy* p = new MACrossStrategy();
     *       delete p;
     * 如果析构函数不是 virtual，delete p 只会调用 IStrategy 的析构函数，
     * MACrossStrategy 的析构函数不会被调用 → 可能内存泄漏。
     * 加了 virtual 后，C++ 会通过虚函数表找到正确的析构函数。
     *
     * = default：让编译器自动生成默认实现。
     */
    virtual ~IStrategy() = default;

    /*
     * name() — 返回策略名称
     * 纯虚函数：每个策略必须告诉我它叫什么。
     * const 表示这个方法不会修改对象状态。
     */
    virtual std::string name() const = 0;

    /*
     * description() — 返回策略描述
     */
    virtual std::string description() const = 0;

    /*
     * param_schema() — 返回策略参数的描述信息
     * 用于 API 返回给前端，让用户知道可以调整哪些参数。
     *
     * std::map 是有序键值对（红黑树实现），
     * 这里 key=参数名, value=默认值描述。
     */
    virtual std::map<std::string, std::string> param_schema() const {
        return {};   // 默认没有参数，子类可以 override
    }

    /*
     * on_init() — 回测开始前调用一次
     * 策略可以在这里做初始化工作，比如清空内部状态。
     * 有默认空实现 {} ，子类不是必须 override。
     */
    virtual void on_init() {}

    /*
     * on_bar() — 核心方法，每根 K 线（每天）调用一次
     *
     * 这是策略的"大脑"：
     * 1. 引擎把当天的状态（ctx）传给策略
     * 2. 策略分析数据，决定要不要交易
     * 3. 返回订单列表（可以是空的 = 今天不交易）
     *
     * = 0：纯虚函数，子类必须实现。
     */
    virtual std::vector<Order> on_bar(const StrategyContext& ctx) = 0;

    /*
     * on_finish() — 回测结束后调用一次
     * 策略可以在这里做清理工作或输出统计信息。
     */
    virtual void on_finish() {}
};

}  // namespace backtest
