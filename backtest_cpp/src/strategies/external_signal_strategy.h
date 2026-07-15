/*
 * external_signal_strategy.h — 外部信号回放策略
 *
 * 与其他策略不同：它自己不做任何技术分析，只是"回放"外部传入的信号序列。
 * 上层（如 alpha_lab 的 AI 生成代码、Python 侧）只负责产出信号
 *   [{date, action, price?, weight?}, ...]，
 * 由本策略在对应日期把信号翻译成 Order，交给回测引擎撮合。
 *
 * 好处：撮合/次日开盘成交/T+1/滑点/费用/全套指标口径全部复用 C++ 引擎，
 * 成为全仓唯一的执行与指标口径（第13步域5「回测统一 C++」的地基）。
 *
 * 信号语义：
 * - action：BUY / SELL（大小写不敏感由 server 侧解析）
 * - weight：BUY 时投入的可用现金比例（默认 0.95，沿用内置策略 position_pct 惯例）
 * - price：给出→限价单（成交价校验），缺省→市价单
 * 成交仍推迟到信号日的次日开盘（防未来函数），与内置策略一致。
 */

#pragma once

#include "backtest/strategy_base.h"
#include <map>

namespace backtest {

/*
 * 单条信号。price 用 has_price 区分"未给出"和"给出 0"。
 */
struct SignalEntry {
    Side side = Side::BUY;   // 买/卖方向
    double weight = 0.95;    // BUY 时投入现金比例
    double price = 0.0;      // 限价单目标价（has_price=false 时忽略）
    bool has_price = false;  // 是否为限价单
};

class ExternalSignalStrategy : public IStrategy {
public:
    /*
     * signals：date → 信号。同一天最多一条（server 侧解析时后者覆盖前者）。
     */
    explicit ExternalSignalStrategy(std::map<std::string, SignalEntry> signals);

    // ── 实现 IStrategy 接口 ──

    std::string name() const override;
    std::string description() const override;
    std::vector<Order> on_bar(const StrategyContext& ctx) override;

private:
    std::map<std::string, SignalEntry> signals_;   // 预注入的信号表
};

}  // namespace backtest
