/*
 * test_pairs_strategy.cpp — 配对交易「两条腿都真的在交易」
 *
 * 🔴 这批测试存在的理由：
 * 旧 PairsStrategy 把第二条腿的 bars 塞进构造函数当只读参考价，
 * 而旧引擎有一句无条件的 `order.symbol = symbol_` 把策略指定的 symbol
 * 强行改回第一条腿 —— **第二条腿从来没下过一张单**，配对交易只有一条腿在动。
 * 那份回测结果看上去完全正常，没有任何报错。
 *
 * 所以这里的断言全部围绕「第二条腿真的成交了吗」，而不是「代码跑通了吗」：
 *   1. trades 里两个 symbol 都要出现，且各自有买有卖
 *   2. z 翻向时要**换腿**（卖掉旧腿 + 买入新腿，同一天）
 *   3. 换腿后新腿的仓位要**用得上卖出腿的回款**（不能缩水成零星几股）
 *   4. 两条腿日期不齐时不崩、也不拿隔天的价格硬凑 z
 *   5. 没有第二条腿的数据时一单不下（老行为不许被改坏）
 *
 * ⚠️ 口径提醒：本策略是 **long-only 两条腿轮动**，不是教科书版的
 *    「买 A + 卖空 B」。引擎不支持做空，理由写在 pairs_strategy.h 顶部。
 */

#include <gtest/gtest.h>
#include "backtest/engine.h"
#include "strategies/pairs_strategy.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <set>

using namespace backtest;

namespace {

/*
 * 一根「开=高=低=收」的 bar。
 * 把价格压平是刻意的：成交发生在**次日开盘**，开收同价才能让每一笔成交
 * 的价格一眼可核，不必在断言里绕开日内波动。
 */
Bar flat(const std::string& date, double px) {
    Bar b;
    b.date = date;
    b.open = b.high = b.low = b.close = px;
    b.volume = 1000000;
    return b;
}

std::string day(int i) {
    char buf[16];
    std::snprintf(buf, sizeof(buf), "2026-01-%02d", i);
    return std::string(buf);
}

/* 把 [价格...] 变成 2026-01-01 起逐日的 bar 序列 */
std::vector<Bar> series(const std::vector<double>& px, int start_day = 1) {
    std::vector<Bar> out;
    for (size_t i = 0; i < px.size(); ++i) {
        out.push_back(flat(day(start_day + static_cast<int>(i)), px[i]));
    }
    return out;
}

/*
 * ⭐ 经典场景（lookback=10, entry_z=2.0, exit_z=0.5，B 恒为 100）
 *
 * 用 Python 把 z 序列逐日算过一遍，落点是确定的：
 *   01-11  z = -2.99  →  空仓 → 持有 A     （买 A 于 01-12 开盘成交）
 *   01-15  z = +2.51  →  持有 A → 持有 B   （卖 A + 买 B 于 01-16 开盘成交）
 *   01-19  z = -0.31  →  持有 B → 空仓     （卖 B 于 01-20 开盘成交）
 * 其余日子 z 都落在 [-2, -0.5] ∪ [0.5, 2] 的缓冲区里 → 维持现状。
 */
const std::vector<double> A_PRICES = {
    100, 101, 100, 101, 100, 101, 100, 101, 100, 101,   // 01-01..01-10 小幅震荡
    85, 85, 85, 85,                                      // 01-11..01-14 A 相对便宜
    140, 140, 140, 140,                                  // 01-15..01-18 A 相对贵
    100, 100, 100                                        // 01-19..01-21 价差回归
};
const std::vector<double> B_PRICES(21, 100.0);

/* crypto 口径：一手 1 股、无最低佣金、无印花税、关滑点，账目一眼可核 */
BacktestEngine crypto_engine(double capital = 100000.0, RiskConfig risk = RiskConfig{}) {
    auto comm = CommissionConfig::crypto();
    comm.slippage_pct = 0.0;
    return BacktestEngine(capital, comm, risk, MarketRules::crypto());
}

/* 取某个 symbol 的全部成交 */
std::vector<Fill> fills_of(const std::vector<Fill>& all, const std::string& sym) {
    std::vector<Fill> out;
    for (const auto& f : all) if (f.symbol == sym) out.push_back(f);
    return out;
}

std::set<std::string> symbols_in(const std::vector<Fill>& all) {
    std::set<std::string> out;
    for (const auto& f : all) out.insert(f.symbol);
    return out;
}

}  // namespace

// ── 1. 第二条腿真的在交易 ────────────────────────────────────────────────

TEST(PairsStrategyTest, BothLegsActuallyTrade) {
    /*
     * 🔴 **整张卡的灵魂断言**：成交记录里两个 symbol 都必须出现。
     * 旧实现在这里必然失败 —— trades 里只有 AAA。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", series(B_PRICES));
    engine.set_strategy(std::make_unique<PairsStrategy>(
        "AAA", "BBB", /*lookback=*/10, /*entry_z=*/2.0, /*exit_z=*/0.5, /*pct=*/0.95));

    auto r = engine.run();

    auto syms = symbols_in(r.trades);
    ASSERT_EQ(syms.size(), 2u)
        << "只有 " << (syms.empty() ? std::string("0") : *syms.begin())
        << " 在交易 —— 第二条腿又变成只读参考价了";
    EXPECT_TRUE(syms.count("AAA"));
    EXPECT_TRUE(syms.count("BBB"));

    // 每条腿都要有买也有卖（不能只是买进去躺着 = 没真的参与轮动）
    for (const char* sym : {"AAA", "BBB"}) {
        auto fs = fills_of(r.trades, sym);
        ASSERT_FALSE(fs.empty()) << sym << " 一笔成交都没有";
        int buys = 0, sells = 0;
        for (const auto& f : fs) (f.side == Side::BUY ? buys : sells) += 1;
        EXPECT_GE(buys, 1) << sym << " 从来没被买进过";
        EXPECT_GE(sells, 1) << sym << " 从来没被卖出过";
    }
}

TEST(PairsStrategyTest, LegRotationHappensOnTheExpectedDates) {
    /*
     * 逐笔核对成交的日期 / 方向 / 价格。
     * z 的触发日与成交日差一天：信号在收盘产生，次日开盘成交（防未来函数）。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", series(B_PRICES));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r = engine.run();

    ASSERT_EQ(r.trades.size(), 4u) << "应当正好是「买A → 卖A+买B → 卖B」四笔";

    // 引擎同一天先卖后买，所以 01-16 那天 SELL AAA 排在 BUY BBB 前面
    EXPECT_EQ(r.trades[0].symbol, "AAA");
    EXPECT_EQ(r.trades[0].side, Side::BUY);
    EXPECT_EQ(r.trades[0].date, "2026-01-12");
    EXPECT_DOUBLE_EQ(r.trades[0].price, 85.0) << "01-11 出信号，01-12 开盘成交";

    EXPECT_EQ(r.trades[1].symbol, "AAA");
    EXPECT_EQ(r.trades[1].side, Side::SELL);
    EXPECT_EQ(r.trades[1].date, "2026-01-16");

    EXPECT_EQ(r.trades[2].symbol, "BBB");
    EXPECT_EQ(r.trades[2].side, Side::BUY);
    EXPECT_EQ(r.trades[2].date, "2026-01-16") << "换腿必须同一天完成，不能拖";

    EXPECT_EQ(r.trades[3].symbol, "BBB");
    EXPECT_EQ(r.trades[3].side, Side::SELL);
    EXPECT_EQ(r.trades[3].date, "2026-01-20") << "|z|<exit_z 之后要清仓拿现金";

    // 收盘时两条腿都不该还留着仓位
    ASSERT_FALSE(r.equity_curve.empty());
    EXPECT_NEAR(r.equity_curve.back().market_value, 0.0, 1e-6)
        << "最后一天应当是空仓（01-19 的 z 已经回到 ±0.5 以内）";
}

TEST(PairsStrategyTest, NewLegUsesProceedsFromTheLegItJustSold) {
    /*
     * 🔴 换腿时新腿的仓位必须**用得上卖出腿的回款**。
     *
     * 卖单要到次日开盘才成交，所以决策当下 ctx.cash 里还没有那笔钱。
     * 若只按 ctx.cash 定量，满仓状态下换腿会算出「几乎没钱」→
     * 新腿只买到零星几股，组合凭空缩水，而且**一句报错都没有**。
     *
     * 这里 01-16 卖出 AAA 拿回约 15.6 万，买 BBB 的名义额必须是这个量级，
     * 而不是决策时 ctx.cash（约 5000）那个量级。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", series(B_PRICES));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r = engine.run();

    auto a_sell = fills_of(r.trades, "AAA");
    auto b_buy = fills_of(r.trades, "BBB");
    ASSERT_EQ(a_sell.size(), 2u);
    ASSERT_FALSE(b_buy.empty());
    const Fill& sold = a_sell[1];               // SELL AAA
    const Fill& bought = b_buy[0];              // BUY BBB
    ASSERT_EQ(sold.side, Side::SELL);
    ASSERT_EQ(bought.side, Side::BUY);

    double proceeds = sold.value();             // 卖 AAA 收回来的钱
    double deployed = bought.value();           // 买 BBB 投出去的钱
    EXPECT_GT(deployed, proceeds * 0.85)
        << "换腿后只投出去 " << deployed << "，而卖出腿回款 " << proceeds
        << " —— 新腿的仓位没用上回款，组合被静默缩水了";
    EXPECT_LE(deployed, proceeds + 100000.0);   // 不许凭空放大杠杆
}

// ── 2. 阈值行为 ──────────────────────────────────────────────────────────

TEST(PairsStrategyTest, StaysFlatWhileSpreadNeverLeavesTheBand) {
    /*
     * 价差始终在 ±entry_z 以内 → 一单都不该下。
     * 这条是上面那批「有成交」断言的对照组：证明成交不是随便什么数据都会有。
     */
    std::vector<double> a(30, 100.0);
    for (size_t i = 0; i < a.size(); ++i) a[i] = (i % 2 == 0) ? 100.0 : 101.0;

    auto engine = crypto_engine();
    engine.load_data("AAA", series(a));
    engine.load_data("BBB", series(std::vector<double>(a.size(), 100.0)));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r = engine.run();
    EXPECT_TRUE(r.trades.empty()) << "价差没离开过带内，不该有任何成交";
}

TEST(PairsStrategyTest, EntryZIsRespectedNotIgnored) {
    /*
     * 同一份数据，把 entry_z 抬到 5.0（高于场景里 z 的最大绝对值 2.99）
     * → 一单都不该下。证明策略真的在读 z 和阈值，而不是照着日期硬编码。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", series(B_PRICES));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 5.0, 0.5, 0.95));

    auto r = engine.run();
    EXPECT_TRUE(r.trades.empty())
        << "entry_z=5 时场景里的 z（最大 |z|≈2.99）不该触发任何入场";
}

// ── 3. 两条腿日期不齐 ────────────────────────────────────────────────────

TEST(PairsStrategyTest, MissingBarOnOneLegSkipsThatDayInsteadOfMisaligning) {
    /*
     * 🔴 **不许假设两条腿等长/日期一一对应**。
     *
     * 这里把 BBB 在 01-11 这天的 bar 挖掉 —— 那正是原场景里 z=-2.99 的入场日。
     * 正确行为：那天**没有可比价差**，不决策；01-12 两条腿都在，才重新算。
     *
     * ⛔ 若实现回到「按 bar 下标配对」，BBB 少一根 bar 会让此后每一天的
     *    价差都由**错位一天**的两个价格算出 —— 而且不会报任何错。
     */
    std::vector<Bar> b = series(B_PRICES);
    b.erase(std::remove_if(b.begin(), b.end(),
                           [](const Bar& x) { return x.date == "2026-01-11"; }),
            b.end());
    ASSERT_EQ(b.size(), B_PRICES.size() - 1);

    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", b);
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r = engine.run();   // ← 首先：不许崩

    // 覆盖天数如实报出来（缺 bar ≠ 数据是 0）
    EXPECT_EQ(r.bar_coverage["AAA"], 21);
    EXPECT_EQ(r.bar_coverage["BBB"], 20);

    // 01-11 缺了 BBB → 那天不决策 → 不可能有 01-12 的成交
    for (const auto& f : r.trades) {
        EXPECT_NE(f.date, "2026-01-12")
            << "01-11 只有一条腿有价格，却还是决策并下单了";
    }
    // 但两条腿都有 bar 的日子仍然照常工作：整段跑下来仍要有真实成交
    EXPECT_FALSE(r.trades.empty()) << "缺一天 bar 不该让整个策略哑掉";
    EXPECT_EQ(symbols_in(r.trades).size(), 2u) << "缺 bar 之后第二条腿又不动了";
}

TEST(PairsStrategyTest, SecondLegListedLaterDoesNotShiftTheSpreadSeries) {
    /*
     * 🔴 **错位回归**：第二条腿「上市晚」——前 6 天没有 bar。
     *
     * 按日期配对：价差序列从 01-07 才开始攒，攒满 10 个要到 01-16，
     * 而原场景的入场日 01-11 那时窗口还没满 → **不该有任何成交**。
     *
     * 而按下标配对（旧写法）：BBB 的第 0 根 bar 会被当成 AAA 的第 0 根，
     * 窗口在 01-10 就「满」了，于是照旧在 01-11 触发入场 —— 用的却是
     * 错开 6 天的两个价格。这条断言就是把这两种行为分开的那把尺子。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", series(std::vector<double>(15, 100.0), /*start_day=*/7));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r = engine.run();

    EXPECT_EQ(r.bar_coverage["BBB"], 15);
    for (const auto& f : r.trades) {
        EXPECT_GE(f.date, "2026-01-17")
            << "第二条腿 01-07 才开始有数据，价差窗口最早 01-16 才攒满，"
               "却在 " << f.date << " 就成交了 —— 价差序列被错位对齐了";
    }
}

// ── 4. 老行为不许被改坏 ──────────────────────────────────────────────────

TEST(PairsStrategyTest, NoSecondLegDataMeansNoTrades) {
    /*
     * 第二条腿的数据没喂进引擎（等价于旧协议里 bars2 为空）→ 一单不下。
     * ⚠️ 这**不是**「没关系」：上游 backend 已经把「取不到第二条腿就 400」
     *    做成硬闸门，这条只是钉住「没有第二条腿时不会瞎交易」。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r = engine.run();
    EXPECT_TRUE(r.trades.empty()) << "只有一条腿也敢下单 = z-score 是编出来的";
}

TEST(PairsStrategyTest, IgnoresSymbolsThatAreNotEitherLeg) {
    /*
     * 组合回测里引擎可能还 load 了别的标的。
     * 策略只认自己那两条腿，绝不能顺手把 CCC 也买了。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", series(B_PRICES));
    engine.load_data("CCC", series(std::vector<double>(21, 50.0)));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r = engine.run();
    for (const auto& f : r.trades) {
        EXPECT_NE(f.symbol, "CCC") << "策略动了不属于这对配对的标的";
    }
    EXPECT_EQ(symbols_in(r.trades).size(), 2u);
}

TEST(PairsStrategyTest, SecondRunOfTheSameStrategyObjectIsIdentical) {
    /*
     * 🔴 策略是**有状态**的（当日缓存 + 价差序列），这在本项目 9 个策略里是头一个。
     * `on_init()` 必须把状态清干净 —— 否则同一个策略对象被 run 第二次时，
     * 价差序列里还压着上一轮的 21 条记录，窗口一开局就是"满"的，
     * 第二次回测会在完全不同的日子交易，而**两次都返回 200、都看不出异常**。
     *
     * 这里刻意复用同一个 engine（策略对象由它独占持有）跑两次。
     */
    auto engine = crypto_engine();
    engine.load_data("AAA", series(A_PRICES));
    engine.load_data("BBB", series(B_PRICES));
    engine.set_strategy(std::make_unique<PairsStrategy>("AAA", "BBB", 10, 2.0, 0.5, 0.95));

    auto r1 = engine.run();
    auto r2 = engine.run();   // 同一个策略对象，第二次

    ASSERT_FALSE(r1.trades.empty());
    ASSERT_EQ(r1.trades.size(), r2.trades.size()) << "on_init() 没把价差序列清干净";
    for (size_t i = 0; i < r1.trades.size(); ++i) {
        EXPECT_EQ(r1.trades[i].symbol, r2.trades[i].symbol);
        EXPECT_EQ(r1.trades[i].date, r2.trades[i].date);
        EXPECT_EQ(r1.trades[i].quantity, r2.trades[i].quantity);
    }
    EXPECT_DOUBLE_EQ(r1.metrics.final_value, r2.metrics.final_value);
}
