/*
 * test_portfolio_engine.cpp — 组合回测引擎（S8）
 *
 * 这批测试盯的是「共享资金池」这件事本身：
 *   1. N 个标的抢同一份钱（逐票独立回测里不存在这回事）
 *   2. 缺 bar 的那天不能把净值弄塌
 *   3. 🔒 总仓位上限与单标的上限是**两条独立约束取更严**，⛔ 不是 max
 *   4. 一手股数按市场配（crypto 一手 = 1 股）
 */

#include <gtest/gtest.h>
#include "backtest/engine.h"
#include "strategies/portfolio_signal_strategy.h"
#include "strategies/external_signal_strategy.h"   // SignalEntry
#include <cmath>

using namespace backtest;

namespace {

/* 造一段价格恒定的日线，省得价格波动干扰对「钱怎么分」的观察 */
std::vector<Bar> flat_bars(const std::vector<std::string>& dates, double px) {
    std::vector<Bar> out;
    for (const auto& d : dates) {
        Bar b;
        b.date = d;
        b.open = b.high = b.low = b.close = px;
        b.volume = 1000000;
        out.push_back(b);
    }
    return out;
}

SignalEntry buy(double weight) {
    SignalEntry e;
    e.side = Side::BUY;
    e.weight = weight;
    return e;
}

const std::vector<std::string> DATES = {
    "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"};

/* 加密货币口径：一手 1 股、无最低佣金、无印花税 */
BacktestEngine crypto_engine(double capital, RiskConfig risk = RiskConfig{}) {
    auto comm = CommissionConfig::crypto();
    comm.slippage_pct = 0.0;   // 关掉滑点，让钱的账目一眼可核
    return BacktestEngine(capital, comm, risk, MarketRules::crypto());
}

}  // namespace

// ── 1. 共享资金池 ────────────────────────────────────────────────────────

TEST(PortfolioEngineTest, TwoSymbolsShareOneWallet) {
    /*
     * ⭐ 这是整张卡的灵魂：两个标的**各要 95% 的现金**，
     * 但账上只有一份钱。花掉的合计不可能超过本金。
     *
     * 逐票独立回测里这个断言是必然失败的 —— 那边每个标的都有完整一份 10 万。
     */
    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars(DATES, 20.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.95)}}},
            {"BBB", {{"2026-01-05", buy(0.95)}}},
        }));

    auto r = engine.run();

    ASSERT_EQ(r.symbols.size(), 2u);
    double spent = 0.0;
    for (const auto& f : r.trades) {
        EXPECT_EQ(f.side, Side::BUY);
        spent += f.price * f.quantity + f.commission;
    }
    EXPECT_GT(spent, 0.0) << "两个标的都该买到一点";
    EXPECT_LE(spent, 100000.0 + 1e-6) << "花的钱不可能超过这一份本金";
    // 两个标的都成交了 —— 等比缩减而不是「先到先得只喂饱第一个」
    EXPECT_EQ(r.trades.size(), 2u);
}

TEST(PortfolioEngineTest, CashContentionIsReportedNotSilent) {
    /*
     * ⛔ 削了单必须说。请求 0.95+0.95 = 190% 远超账上现金，
     * 引擎按名义额等比缩减，`cash_contention` 要如实报出来。
     */
    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.95)}}},
            {"BBB", {{"2026-01-05", buy(0.95)}}},
        }));

    auto r = engine.run();
    EXPECT_GE(r.cash_contention_days, 1) << "抢钱这件事发生了，就得记一笔";
    EXPECT_GT(r.cash_contention_trimmed, 0.0);
}

TEST(PortfolioEngineTest, NoContentionWhenRequestsFit) {
    /* 请求合计装得下时不该误报「抢钱了」 */
    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.3)}}},
            {"BBB", {{"2026-01-05", buy(0.3)}}},
        }));

    auto r = engine.run();
    EXPECT_EQ(r.cash_contention_days, 0);
    EXPECT_DOUBLE_EQ(r.cash_contention_trimmed, 0.0);
}

TEST(PortfolioEngineTest, TrimmingIsProportionalNotFirstComeFirstServed) {
    /*
     * 🔴 不许「先到先得」——那样谁买得到由 std::map 的字母序决定，
     * 排在前面的标的永远压着后面的，是个看不见的系统性偏袒。
     *
     * 两个标的同价同权重 → 缩减后拿到的钱应当基本相等。
     */
    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("ZZZ", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.9)}}},
            {"ZZZ", {{"2026-01-05", buy(0.9)}}},
        }));

    auto r = engine.run();
    ASSERT_EQ(r.trades.size(), 2u);
    double a = 0.0, z = 0.0;
    for (const auto& f : r.trades) {
        (f.symbol == "AAA" ? a : z) += f.price * f.quantity;
    }
    EXPECT_GT(a, 0.0);
    EXPECT_GT(z, 0.0);
    // 同价同权重，差异只该来自取整
    EXPECT_NEAR(a, z, std::max(a, z) * 0.02);
}

// ── 2. 缺 bar 不是 0 ─────────────────────────────────────────────────────

TEST(PortfolioEngineTest, MissingBarDoesNotCollapseEquity) {
    /*
     * 🔴 不同标的交易日历不齐（crypto 7×24 vs 股票；新币上市晚；停牌）。
     * 某标的某天没有 bar 时，它的持仓市值必须**沿用上一次价格**，
     * ⛔ 绝不能当成 0 —— 那会让净值曲线在缺 bar 那天凭空塌陷。
     */
    auto engine = crypto_engine(100000.0);
    // AAA 全周有数据；BBB 缺 01-07、01-08
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars({"2026-01-05", "2026-01-06", "2026-01-09"}, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"BBB", {{"2026-01-05", buy(0.5)}}},
        }));

    auto r = engine.run();

    ASSERT_EQ(r.equity_curve.size(), DATES.size());
    EXPECT_EQ(r.bar_coverage["AAA"], 5);
    EXPECT_EQ(r.bar_coverage["BBB"], 3) << "覆盖天数要能被看见";

    // 价格恒定 + 无滑点 → 每天净值都该在本金附近（只差手续费），一天都不该塌
    for (const auto& snap : r.equity_curve) {
        EXPECT_GT(snap.total_value, 99000.0)
            << snap.date << " 那天净值塌了 —— 多半是把缺失的 bar 当成 0 了";
    }
}

// ── 3. 🔒 两条仓位上限取更严 ──────────────────────────────────────────────

TEST(PortfolioEngineTest, TotalPositionCapKeepsCashEvenWhenSingleCapIsLoose) {
    /*
     * 🔒 **这条是现金保护的门禁**。
     *
     * 单标的上限 0.95（很松）、总仓位上限 0.8（要留 20% 现金）。
     * 正确行为 = 两条独立约束**取更严**，所以持仓最多 80%。
     *
     * ⛔ 如果有人把它写成 max(0.8, 0.95) = 0.95，这条测试会红 ——
     *    那个写法会**静默撤销 20% 现金保护**，Python 侧被复制过三份，
     *    教训见 `risk-total-position-floor`。
     */
    RiskConfig risk;
    risk.enabled = true;
    risk.max_position_pct = 0.95;
    risk.max_total_position_pct = 0.8;

    auto engine = crypto_engine(100000.0, risk);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.95)}}},
            {"BBB", {{"2026-01-06", buy(0.95)}}},
        }));

    auto r = engine.run();

    ASSERT_FALSE(r.equity_curve.empty());
    for (const auto& snap : r.equity_curve) {
        double pos_ratio = snap.market_value / snap.total_value;
        EXPECT_LE(pos_ratio, 0.8 + 0.01)
            << snap.date << " 持仓占到 " << pos_ratio
            << " —— 20% 现金保护被撤销了";
    }
}

TEST(PortfolioEngineTest, SingleSymbolCapCountsExistingPosition) {
    /*
     * ⚠️ 单标的上限必须把**已有持仓**算进去。
     * 旧实现只看这一笔订单的名义额，于是「今天买 20%、明天再买 20%」
     * 能一路堆到 40%，而闸门全程放行。
     */
    RiskConfig risk;
    risk.enabled = true;
    risk.max_position_pct = 0.25;

    auto engine = crypto_engine(100000.0, risk);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.9)}, {"2026-01-06", buy(0.9)},
                     {"2026-01-07", buy(0.9)}}},
        }));

    auto r = engine.run();
    for (const auto& snap : r.equity_curve) {
        EXPECT_LE(snap.market_value / snap.total_value, 0.25 + 0.01)
            << snap.date << " 单标的仓位堆过头了";
    }
}

// ── 4. 一手股数按市场配 ──────────────────────────────────────────────────

TEST(PortfolioEngineTest, CryptoLotSizeIsOneSoHighPricedCoinsCanTrade) {
    /*
     * 🔴 「一手 = 100 股」是 A 股假设。BTC 单价 6 万+，
     * 按 100 股一手算，$10 万本金**连一手都凑不齐 → 零成交**。
     * crypto 的一手必须是 1 股。
     */
    auto engine = crypto_engine(100000.0);
    engine.load_data("BTC", flat_bars(DATES, 64000.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"BTC", {{"2026-01-05", buy(0.9)}}},
        }));

    auto r = engine.run();
    ASSERT_EQ(r.trades.size(), 1u) << "一手 1 股才买得起 6 万块的币";
    EXPECT_EQ(r.trades[0].quantity, 1);
}

TEST(PortfolioEngineTest, AShareLotSizeStaysAtOneHundred) {
    /* ⛔ 别把 A 股也顺手改成 1 股一手 —— 那是真实的交易所规则 */
    auto comm = CommissionConfig::a_share();
    comm.slippage_pct = 0.0;
    BacktestEngine engine(100000.0, comm, RiskConfig{}, MarketRules::a_share());
    engine.load_data("600000", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"600000", {{"2026-01-05", buy(0.5)}}},
        }));

    auto r = engine.run();
    ASSERT_EQ(r.trades.size(), 1u);
    // ⛔ 别只断言 `% 100 == 0` —— 一手改成 1 股时算出来的 5000 照样整除 100，
    //    那条断言**结构上不可能**测出这个改坏（审查实测：改了也绿）。
    EXPECT_EQ(r.trades[0].quantity, 5000) << "10 万 × 50% ÷ 10 元 = 5000 股";
}


TEST(PortfolioEngineTest, AShareBelowOneLotDoesNotTrade) {
    /* 不足一手不发单 —— 一手被改小的话这条会红 */
    auto comm = CommissionConfig::a_share();
    comm.slippage_pct = 0.0;
    BacktestEngine engine(500.0, comm, RiskConfig{}, MarketRules::a_share());   // 只够买 50 股
    engine.load_data("600000", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"600000", {{"2026-01-05", buy(0.9)}}},
        }));

    auto r = engine.run();
    EXPECT_TRUE(r.trades.empty()) << "450 元买不起一手（100 股 × 10 元），不该成交";
}


TEST(PortfolioEngineTest, LotFloorRoundsDownNeverUp) {
    /*
     * ⚠️ `lot_floor` 必须**向下**取整。向上取整曾经能蒙混过关，因为多买的量
     * 又被资金竞争的等比缩减削回来了 —— 所以这条刻意只放**一个**标的、
     * 且请求量装得下（不触发缩减），把掩护撤掉。
     */
    auto comm = CommissionConfig::a_share();
    comm.slippage_pct = 0.0;
    comm.min_commission = 0.0;
    BacktestEngine engine(100000.0, comm, RiskConfig{}, MarketRules::a_share());
    engine.load_data("600000", flat_bars(DATES, 30.0));   // 10万×0.5÷30 = 1666.6 股
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"600000", {{"2026-01-05", buy(0.5)}}},
        }));

    auto r = engine.run();
    ASSERT_EQ(r.trades.size(), 1u);
    EXPECT_EQ(r.trades[0].quantity, 1600) << "1666.6 股要向下取到 1600，不是 1700";
}

// ── 5. 单标的路径不许被改坏 ──────────────────────────────────────────────

TEST(PortfolioEngineTest, SingleSymbolStillReportsThatSymbol) {
    /* 只 load 一个标的时，result.symbol 仍是那个标的（老调用方依赖它） */
    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{}));

    auto r = engine.run();
    EXPECT_EQ(r.symbol, "AAA");
    ASSERT_EQ(r.symbols.size(), 1u);
    EXPECT_EQ(r.symbols[0], "AAA");
}

TEST(PortfolioEngineTest, StrategySetSymbolIsNotOverwritten) {
    /*
     * 🔴 旧引擎有一句无条件的 `order.symbol = symbol_`，
     * 把策略自己设的 symbol **强行覆盖掉**。于是 PairsStrategy 那种
     * 想下第二条腿的策略，单子会被改成第一只标的 ——
     * 另一条腿从来没真的交易过。
     */
    class CrossLegStrategy : public IStrategy {
    public:
        std::string name() const override { return "CROSS"; }
        std::string description() const override { return "在 AAA 的 bar 上给 BBB 下单"; }
        std::vector<Order> on_bar(const StrategyContext& ctx) override {
            std::vector<Order> out;
            if (ctx.symbol == "AAA" && ctx.current_bar.date == "2026-01-05") {
                out.push_back(Order::market_buy("BBB", 100));   // ← 显式指定别的标的
            }
            return out;
        }
    };

    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<CrossLegStrategy>());

    auto r = engine.run();
    ASSERT_EQ(r.trades.size(), 1u);
    EXPECT_EQ(r.trades[0].symbol, "BBB") << "策略指定的 symbol 被引擎覆盖了";
}

// ── 6. 审查补的回归（B1/B2/M13/M15/m8）─────────────────────────────────────

TEST(PortfolioEngineTest, TotalCapHoldsWhenSeveralBuysLandOnTheSameDay) {
    /*
     * 🔴 **这是审查挖出来的 blocker**。
     *
     * 闸门是无状态的：它拿「当前总仓位」判额度，但不会替调用方扣。
     * 引擎若只在开盘时算一次快照、然后每个买单都拿这份快照去过闸门，
     * 那么 N 个买单**各自**都在 80% 以内、**合计**能顶到 100%。
     *
     * 实测过：三个标的同日各请求 35%，现金被打到 0.1 块。
     *
     * ⚠️ 与 `TotalPositionCapKeepsCash…` 的区别就在**同一天**——
     * 那条把两个买单排在不同的两天，恰好是这个 bug 唯一不咬人的排法。
     */
    RiskConfig risk;
    risk.enabled = true;
    risk.max_position_pct = 1.0;          // 单标的不限，只留总仓位上限
    risk.max_total_position_pct = 0.8;

    auto engine = crypto_engine(100000.0, risk);
    for (const char* sym : {"AAA", "BBB", "CCC"}) {
        engine.load_data(sym, flat_bars(DATES, 10.0));
    }
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.35)}}},
            {"BBB", {{"2026-01-05", buy(0.35)}}},
            {"CCC", {{"2026-01-05", buy(0.35)}}},
        }));

    auto r = engine.run();
    ASSERT_FALSE(r.equity_curve.empty());
    for (const auto& snap : r.equity_curve) {
        EXPECT_LE(snap.market_value / snap.total_value, 0.8 + 0.01)
            << snap.date << " 同日三单合计把总仓位顶穿了（每单单看都合规）";
        EXPECT_GT(snap.cash, snap.total_value * 0.19)
            << snap.date << " 20% 现金没留住";
    }
}

TEST(PortfolioEngineTest, TotalCapUsesTodaysOpenNotYesterdaysClose) {
    /*
     * 🔴 审查挖出的第二个 blocker：**价基混用**。
     *
     * 闸门核验用今日开盘价，但「当前总仓位」若取自 portfolio（此刻还是**昨收**
     * 标记的），隔夜跳空时两者对不上：实测持仓隔夜从 10 跳到 40，账面已经 92%，
     * 闸门按昨收算还觉得有三万块空间，照放。
     */
    RiskConfig risk;
    risk.enabled = true;
    risk.max_total_position_pct = 0.8;

    // BBB 在 01-07 跳空到 40（前两天 10）
    std::vector<Bar> bbb = flat_bars(DATES, 10.0);
    for (size_t i = 2; i < bbb.size(); ++i) {
        bbb[i].open = bbb[i].high = bbb[i].low = bbb[i].close = 40.0;
    }

    auto engine = crypto_engine(100000.0, risk);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", std::move(bbb));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"BBB", {{"2026-01-05", buy(0.5)}}},    // 先建仓
            {"AAA", {{"2026-01-06", buy(0.5)}}},    // 跳空当天再买
        }));

    auto r = engine.run();
    for (const auto& snap : r.equity_curve) {
        EXPECT_LE(snap.market_value / snap.total_value, 0.8 + 0.02)
            << snap.date << " 跳空后闸门用了过时的价基";
    }
}

TEST(PortfolioEngineTest, PositionCapsWorkEvenWhenRiskIsDisabled) {
    /*
     * 🔒 `enabled` 管的是**止损**那套，仓位上限是**账户结构约束**。
     * 绑在一起的后果：`enabled=false`（服务端默认值）时 20% 现金保护
     * 配了等于没配，而且看不出来。
     */
    RiskConfig risk;
    risk.enabled = false;                 // ← 止损关着
    risk.max_total_position_pct = 0.8;    // ← 但现金底线要照守

    auto engine = crypto_engine(100000.0, risk);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.99)}}},
        }));

    auto r = engine.run();
    for (const auto& snap : r.equity_curve) {
        EXPECT_LE(snap.market_value / snap.total_value, 0.8 + 0.01)
            << snap.date << " 止损一关，现金保护就没了";
    }
}

TEST(PortfolioEngineTest, ContextCashIsTheSharedBalanceNotAPerSymbolWallet) {
    /*
     * ⭐ **S8 的核心契约**：`ctx.cash` 是全场共享余额，不是「这个标的那份钱」。
     *
     * 审查发现此前没有任何测试盯着它 —— `TwoSymbolsShareOneWallet` 断言的是
     * 「花掉的钱 ≤ 本金」，那是**撮合层**兜住的，即使每个标的各看到一份完整
     * 本金也照样绿。这条直接看策略拿到的 ctx.cash。
     */
    struct CashSpy : IStrategy {
        std::map<std::string, double> seen;
        std::string name() const override { return "SPY"; }
        std::string description() const override { return "记下每个标的看到的现金"; }
        std::vector<Order> on_bar(const StrategyContext& ctx) override {
            if (ctx.current_bar.date == "2026-01-06") seen[ctx.symbol] = ctx.cash;
            std::vector<Order> out;
            if (ctx.symbol == "AAA" && ctx.current_bar.date == "2026-01-05") {
                out.push_back(Order::market_buy("AAA", 5000));   // 花掉 5 万
            }
            return out;
        }
    };
    auto spy = std::make_unique<CashSpy>();
    CashSpy* raw = spy.get();

    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars(DATES, 10.0));
    engine.set_strategy(std::move(spy));
    engine.run();

    ASSERT_EQ(raw->seen.count("BBB"), 1u);
    EXPECT_LT(raw->seen["BBB"], 60000.0)
        << "BBB 看到的还是满额本金 —— 共享资金池退化成了每标的一个钱包";
    EXPECT_NEAR(raw->seen["AAA"], raw->seen["BBB"], 1.0)
        << "同一天同一份余额，两个标的看到的应该一样";
}

TEST(PortfolioEngineTest, OrdersOnASymbolThatStopsHavingBarsAreCountedSeparately) {
    /*
     * 某标的的数据半途就断了，它那天产生的挂单再也等不到「下一根 bar 的开盘」。
     * 顺延是对的（不能丢），但收尾时**不能跟「最后一天的挂单」混在一起数** ——
     * 混着数会让日志说出「最后一 bar 有 1 笔挂单被丢弃」这种假话，
     * 而它其实是三天前就挂死了。
     */
    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.load_data("BBB", flat_bars({"2026-01-05", "2026-01-06"}, 10.0));   // 数据早断
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"BBB", {{"2026-01-06", buy(0.5)}}},   // 最后一根 bar 才出信号 → 永远等不到成交
        }));

    auto r = engine.run();
    EXPECT_EQ(r.trades.size(), 0u);
    EXPECT_EQ(r.dropped_stale_orders, 1) << "挂死的单要单独计";
    EXPECT_EQ(r.dropped_last_bar_orders, 0) << "它不是最后一天产生的，别混进来";
}

TEST(PortfolioEngineTest, SingleBuyHittingCashLimitIsNotCalledContention) {
    /*
     * ⚠️ 「资金竞争」指的是**两个以上买单在抢**。
     * 判据若只写 `need > cash`，单标的请求 100% 现金、手续费一顶就超，
     * 也会被记成「抢钱了」—— 这个字段是给 Jason 看「几天出现过抢钱」的，
     * 对任何满仓请求都报警等于没用。
     */
    auto engine = crypto_engine(100000.0);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(1.0)}}},   // 要 100% 现金，加手续费必超
        }));

    auto r = engine.run();
    EXPECT_EQ(r.cash_contention_days, 0) << "只有一个买单，谈不上竞争";
}

TEST(PortfolioEngineTest, ExplicitOddLotSurvivesWhenNoCapReducesIt) {
    /*
     * ⚠️ 整手取整只在**风控真的削了单**的时候才做。
     *
     * 策略显式下 137 股、又没配任何仓位上限时，旧引擎原样放行；
     * 若把取整无条件写进风控块，这单会被砍成 100 —— 那不是风控该管的事
     * （交易单位归策略层的 `ctx.lot_floor` 管），纯粹是代码块放错了位置。
     */
    struct OddLot : IStrategy {
        std::string name() const override { return "ODD"; }
        std::string description() const override { return "显式下 137 股"; }
        std::vector<Order> on_bar(const StrategyContext& ctx) override {
            std::vector<Order> out;
            if (ctx.current_bar.date == "2026-01-05") {
                out.push_back(Order::market_buy(ctx.symbol, 137));
            }
            return out;
        }
    };
    RiskConfig risk;
    risk.enabled = true;            // 止损开着
    // 但两条仓位上限都不配（保持默认 1.0）

    auto comm = CommissionConfig::a_share();
    comm.slippage_pct = 0.0;
    BacktestEngine engine(100000.0, comm, risk, MarketRules::a_share());
    engine.load_data("600000", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<OddLot>());

    auto r = engine.run();
    ASSERT_EQ(r.trades.size(), 1u);
    EXPECT_EQ(r.trades[0].quantity, 137) << "没有任何上限削它，不该被顺手取整";
}

// ── 7. 🔴 总仓位额度必须等比分配，不能字母序先到先得 ──────────────────────

TEST(PortfolioEngineTest, TotalCapHeadroomIsSharedProportionallyNotAlphabetically) {
    /*
     * 🔴 **审查挖出的 blocker，而且是卡片 §2.1 明令要避免的那件事。**
     *
     * 第一版把总仓位额度写成「逐单先到先得」：每放行一单就把名义额累加进
     * total_pos_now，下一单看到的额度就少了。于是谁买得到**由 std::map 的
     * 字母序决定** —— 真实 universe 里 BTCUSDT.BN < ETHUSDT.BN < SOLUSDT.BN，
     * BTC 永远赢、SOL 永远被饿死。
     *
     * 实测 8 个币同日各请求 95%（15% 单币 / 80% 总仓）：
     *   前五个各拿 15%、第六个拿 5%、**最后两个一单都没买到**。
     *
     * 更糟的是它能把回测结论翻号：把赢家改个名排到字母表前面，
     * net_return 从 −2.71% 变 +3.88%，`passed` 跟着从 false 翻 true。
     */
    RiskConfig risk;
    risk.enabled = true;
    risk.max_position_pct = 0.15;
    risk.max_total_position_pct = 0.80;

    auto engine = crypto_engine(100000.0, risk);
    std::map<std::string, std::map<std::string, SignalEntry>> sigs;
    for (const char* sym : {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"}) {
        engine.load_data(sym, flat_bars(DATES, 10.0));
        sigs[sym] = {{"2026-01-05", buy(0.95)}};
    }
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(std::move(sigs)));

    auto r = engine.run();

    std::map<std::string, double> got;
    for (const auto& f : r.trades) got[f.symbol] += f.price * f.quantity;
    EXPECT_EQ(got.size(), 8u) << "有币被字母序饿死了（一单都没买到）";

    // 8 个币平分 80% 额度 → 每个约 10%，差异只该来自取整
    double lo = 1e18, hi = 0.0;
    for (const auto& [sym, v] : got) { lo = std::min(lo, v); hi = std::max(hi, v); }
    EXPECT_LT(hi - lo, hi * 0.05) << "分配不均 —— 排在前面的币多吃了";
}

TEST(PortfolioEngineTest, PositionCapContentionIsReportedSeparatelyFromCash) {
    /*
     * ⛔ **额度卡住时账上现金还剩着**，所以 `cash_contention` 一天都不会记。
     * 第一版就是这样：后几个币一单买不到，而唯一的诊断字段是 0 ——
     * 整件事全程静默。必须单独一个计数。
     */
    RiskConfig risk;
    risk.enabled = true;
    risk.max_total_position_pct = 0.5;

    auto engine = crypto_engine(100000.0, risk);
    std::map<std::string, std::map<std::string, SignalEntry>> sigs;
    for (const char* sym : {"AAA", "BBB"}) {
        engine.load_data(sym, flat_bars(DATES, 10.0));
        sigs[sym] = {{"2026-01-05", buy(0.45)}};   // 合计 90% > 50% 额度
    }
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(std::move(sigs)));

    auto r = engine.run();
    EXPECT_GE(r.cap_contention_days, 1) << "被仓位上限削了单，必须记一笔";
    EXPECT_GT(r.cap_contention_trimmed, 0.0);
    EXPECT_EQ(r.cash_contention_days, 0) << "现金还剩着，别混成资金竞争";
}

TEST(PortfolioEngineTest, SingleSymbolCapTrimmingIsReported) {
    /*
     * ⛔ **单标的上限裁掉多少必须报出来。**
     *
     * 它跟 `cap_contention`（额度不够、多单等比分摊）是两件事：这里没有竞争，
     * 就是一条上限把单直接裁小。但对使用者来说同样是「我请求的名义额没全成交」，
     * 而且影响可以非常大 —— 实测单币 15% 上限把请求削掉约 85%，收益率缩到 1/6。
     * 不报的话屏幕上只剩一个「收益 -1.3%」，看不出它是被上限压出来的。
     *
     * 🔴 这是审查重跑生产数据时挖出来的：`cap_contention` 是 0、caveats 是空，
     *    而 15% 上限明明在起作用 —— 两个「没事」拼出一个假象。
     */
    RiskConfig risk;
    risk.enabled = true;
    risk.max_position_pct = 0.15;      // 单币上限
    risk.max_total_position_pct = 1.0; // 总仓位不限，隔离出单币上限这一条

    auto engine = crypto_engine(100000.0, risk);
    engine.load_data("AAA", flat_bars(DATES, 10.0));
    engine.set_strategy(std::make_unique<PortfolioSignalStrategy>(
        std::map<std::string, std::map<std::string, SignalEntry>>{
            {"AAA", {{"2026-01-05", buy(0.95)}}},   // 想要 95%，只能拿 15%
        }));

    auto r = engine.run();
    EXPECT_GE(r.symbol_cap_days, 1) << "单币上限削了单，必须记一笔";
    EXPECT_GT(r.symbol_cap_trimmed, 0.0);
    EXPECT_EQ(r.cap_contention_days, 0) << "总仓位不限，别混成额度竞争";
    EXPECT_EQ(r.cash_contention_days, 0) << "只有一个买单，谈不上抢钱";
}
