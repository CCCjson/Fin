/*
 * engine.cpp — 回测引擎的实现
 *
 * 这是整个回测系统最核心的文件。
 * run() 实现了「逐日、多标的、共享一份现金」的回测循环。
 *
 * ⭐ **共享资金池是这个引擎的灵魂**（S8）：
 * 从前每个标的各跑一遍、各发一份完整本金，再把收益率平均 —— 那不是组合回测，
 * 没有资金竞争，也就没有「A 占了钱 B 就买不了」。带权重的组合策略在那种口径下
 * 回测出来的数字**跟权重毫无关系**。
 *
 * 现在：一个 Portfolio、一份 cash_、所有标的抢它。
 */

#include "backtest/engine.h"
#include "backtest/strategy_context.h"
#include <algorithm>   // std::find_if, std::sort
#include <iostream>    // std::cerr
#include <set>
#include <stdexcept>   // std::runtime_error
#include <unordered_map>

namespace backtest {

BacktestEngine::BacktestEngine(double initial_capital, CommissionConfig commission,
                               RiskConfig risk_config, MarketRules market_rules)
    : initial_capital_(initial_capital)
    , commission_config_(std::move(commission))
    , risk_config_(risk_config)
    , market_rules_(market_rules)
{
}

/*
 * set_strategy — 接管策略对象的所有权
 *
 * ⚠️ 组合回测下**所有标的共用这一个策略实例**。
 * 策略如果在成员变量里存了跨 bar 的状态（比如 KDJ 的前值），那份状态会被
 * 所有标的共享 —— 这对无状态策略（DSL 回放 / 均线 / MACD 都是从 history 现算的）
 * 没有影响，但有状态的策略在多标的下会串味。
 * 现有 9 个策略都是从 ctx.history 现算的，故安全；新增有状态策略时要留意。
 */
void BacktestEngine::set_strategy(std::unique_ptr<IStrategy> strategy) {
    strategy_ = std::move(strategy);
}

void BacktestEngine::load_data(const std::string& symbol, std::vector<Bar> bars) {
    if (bars_.find(symbol) == bars_.end()) {
        load_order_.push_back(symbol);
    }
    bars_[symbol] = std::move(bars);
}

namespace {

/* 一个标的在某一天的运行态 */
struct SymbolState {
    std::vector<Bar> history;                       // 到今天为止的全部 bar（策略算指标用）
    std::unordered_map<std::string, size_t> index;  // date → bars 下标
};

}  // namespace

/*
 * run — 回测核心循环
 *
 * 【真实成交模型】信号在 bar i 收盘产生，成交推迟到 bar i+1 开盘（next-bar-open）：
 *   杜绝"当日收盘出信号、又按当日收盘价成交"的未来函数（look-ahead bias）；
 *   并配合 settle_t1() 强制 A 股 T+1（当日买入次日才可卖）。
 *
 * 每天的顺序（顺序本身是有讲究的，别随手调）：
 *   1. settle_t1        —— 昨日及更早买入的持仓解冻为可卖
 *   2. 执行昨天挂的单    —— **先卖后买**，按各标的今日开盘价
 *   3. update_price     —— 用今日收盘价标记持仓市值（只标今天有 bar 的标的）
 *   4. 逐标的决策        —— 止损优先，然后 strategy->on_bar()
 *   5. record_equity    —— 记一笔净值
 */
BacktestResult BacktestEngine::run(const std::string& start_date,
                                    const std::string& end_date) {
    // ── Step 1: 参数验证 ──
    if (!strategy_) {
        throw std::runtime_error("No strategy set. Call set_strategy() first.");
    }
    if (bars_.empty()) {
        throw std::runtime_error("No data loaded. Call load_data() first.");
    }

    // ── Step 2: 创建投资组合（一份现金，全场共享）──
    Portfolio portfolio(initial_capital_, commission_config_);

    /*
     * ── Step 3: 联合交易日历 ──
     *
     * 🔴 不同标的的交易日历**不一定齐**：crypto 7×24 而股票有周末；新币上市晚；
     * 股票会停牌。所以推进的单位必须是**日期**，不能是「第几根 bar」——
     * 后者会让两个标的的第 i 根 bar 对应到不同的日子，净值曲线直接错位。
     *
     * ⛔ 某标的当天没有 bar → **跳过它当天的决策**，持仓市值沿用上一次的价格。
     *    绝不能把缺失当成 0，那会让净值在缺 bar 那天凭空塌陷。
     */
    std::set<std::string> date_set;
    std::map<std::string, SymbolState> state;
    int duplicate_dates = 0;
    for (const auto& [sym, bars] : bars_) {
        auto& st = state[sym];
        for (size_t i = 0; i < bars.size(); ++i) {
            const std::string& d = bars[i].date;
            if (!start_date.empty() && d < start_date) continue;
            if (!end_date.empty() && d > end_date) continue;
            date_set.insert(d);
            /*
             * 🔴 同一标的同一天出现两根 bar 是**数据坏了**（日线不该有重复日期）。
             * 这里只能保留一根，但**绝不静默**：按下标推进的旧引擎看不见这件事，
             * 于是「100 根 bar 只有 94 个日期」这种数据能一路跑到底、
             * 结果看上去完全正常。数出来随结果返回。
             */
            if (st.index.count(d)) ++duplicate_dates;
            st.index[d] = i;
        }
    }
    std::vector<std::string> dates(date_set.begin(), date_set.end());
    if (dates.empty()) {
        throw EmptyDateRange("No bars fall inside the requested date range.");
    }

    // ── Step 4: 初始化策略和风控 ──
    strategy_->on_init();
    RiskManager risk_mgr(risk_config_);

    std::vector<Order> pending;   // 昨天产生、等今天开盘成交的订单（策略单与止损单共用）
    int cash_contention_days = 0;
    double cash_contention_trimmed = 0.0;
    int cap_contention_days = 0;
    double cap_contention_trimmed = 0.0;
    int symbol_cap_days = 0;
    double symbol_cap_trimmed = 0.0;

    for (size_t di = 0; di < dates.size(); ++di) {
        const std::string& today = dates[di];

        // ── (1) T+1 结算 ──
        portfolio.settle_t1();

        // ── (2) 执行挂单：先卖后买 ──
        /*
         * **先卖后买**是刻意的：卖出释放的现金，同一天就能被买单用上。
         * A 股的 T+1 限制的是**股票**（`settle_t1` 在管），不是现金。
         */
        auto open_of = [&](const std::string& sym) -> const Bar* {
            auto sit = state.find(sym);
            if (sit == state.end()) return nullptr;
            auto iit = sit->second.index.find(today);
            if (iit == sit->second.index.end()) return nullptr;
            return &bars_[sym][iit->second];
        };

        std::vector<Order> sells, buys;
        std::vector<Order> carried;   // 今天没有 bar 的标的：挂单顺延，不丢
        for (auto& o : pending) {
            const Bar* b = open_of(o.symbol);
            if (!b) { carried.push_back(o); continue; }
            (o.side == Side::SELL ? sells : buys).push_back(o);
        }
        pending.clear();

        for (auto& order : sells) {
            const Bar* b = open_of(order.symbol);
            auto fill = portfolio.execute_order(order, b->open, today);
            if (fill) {
                if (order.order_id.rfind("RISK_", 0) == 0) {
                    std::string reason = order.order_id.length() > 5
                        ? order.order_id.substr(5) : "stop_loss";
                    portfolio.set_last_fill_reason(reason);
                }
                if (!portfolio.has_position(order.symbol)) {
                    risk_mgr.reset_tracking(order.symbol);
                }
            }
        }

        /*
         * 买单：先过风控闸门（用**实际成交价**即今日开盘价重新核验，而不是信号
         * 产生时的收盘价——否则隔夜跳空高开会让实际仓位击穿上限），
         * 再处理**资金竞争**。
         */
        /*
         * 🔴 **价基必须统一到今日开盘**。
         *
         * `portfolio.get_market_value()` 此刻还是**昨收**标记的（今天的
         * `update_price` 在第 (3) 步才跑），而闸门核验用的是今日开盘价。
         * 两个价基混用 → 隔夜跳空时闸门会拿一个过时的总仓位去判额度：
         * 实测 BBB 隔夜从 10 跳到 40，账面已经 92% 了，闸门按昨收算还觉得
         * 有三万块空间，照放。所以这里按今日开盘价重新标一遍。
         */
        auto mark_price_of = [&](const std::string& sym, const Position& pos) {
            const Bar* b = open_of(sym);
            return b ? b->open : pos.current_price;   // 今天没 bar 的沿用上次价
        };
        double total_pos_now = 0.0;
        for (const auto& [sym, pos] : portfolio.get_positions()) {
            total_pos_now += pos.quantity * mark_price_of(sym, pos);
        }
        double total_value_now = portfolio.get_cash() + total_pos_now;

        int lot = market_rules_.lot_size > 0 ? market_rules_.lot_size : 1;

        /*
         * ── (2a) 单标的上限：**逐个独立判**，标的之间不争 ──
         * 「本标的持仓不得超过总资产 X%」是每个标的自己的事，没有竞争关系，
         * 所以这一步可以逐单直接裁，顺序无关。
         */
        double sym_cap_trimmed = 0.0;
        for (auto& order : buys) {
            const Bar* b = open_of(order.symbol);
            if (!risk_mgr.has_position_caps()) break;
            const Position* p = portfolio.get_position(order.symbol);
            double sym_val = p ? p->quantity * b->open : 0.0;
            // 只过 ① 这一条：总仓位上限留到 (2b) 统一分配
            int adjusted = risk_mgr.filter_symbol_quantity(
                order.quantity, b->open, total_value_now, sym_val);
            /*
             * ⛔ **单标的上限裁掉多少，必须报出来。**
             * 它跟 (2b) 的「额度不够、多单等比分摊」是两件事：这里没有竞争，
             * 就是一条上限直接把单裁小。但对使用者来说它同样是「我请求的
             * 名义额没有全部成交」，而且影响可以非常大 —— 实测单币上限 15%
             * 会把请求削掉约 85%，收益率跟着缩到 1/6。
             * 不报的话，屏幕上只剩一个「收益 -1.3%」，看不出它是被上限压出来的。
             */
            if (adjusted < order.quantity) {
                sym_cap_trimmed += (order.quantity - std::max(0, adjusted)) * b->open;
            }
            /*
             * ⚠️ 只在**被削减时**才取整到手。
             * 无条件取整会顺手改掉一件无关的事：策略显式下 137 股、又没配
             * 任何上限时，旧引擎原样放行，无条件取整会砍成 100 ——
             * 那不是风控该管的，交易单位归策略层（ctx.lot_floor）管。
             */
            if (adjusted < order.quantity) adjusted = (adjusted / lot) * lot;
            order.quantity = std::max(0, adjusted);
        }

        /*
         * ── (2b) 🔴 总仓位上限：**这是一份公共额度，必须等比分配** ──
         *
         * 这里踩过一个很隐蔽的坑：第一版把总仓位额度写成「逐单先到先得」
         * （每放行一单就把名义额累加进 total_pos_now，下一单看到的额度就少了）。
         * 那样谁买得到**由 std::map 的字母序决定** —— 真实 universe 里
         * `BTCUSDT.BN < ETHUSDT.BN < SOLUSDT.BN`，**BTC 永远赢，SOL 永远被饿死**。
         *
         * 实测 8 个币同日各请求 95%（上限 15% 单币 / 80% 总仓）：
         *   前五个各拿 15%、第六个拿 5%、最后两个**一单都没买到**。
         * 而下面那段资金竞争**完全不会触发**（现金还剩着，卡住的是仓位不是钱），
         * 于是 `cash_contention.days = 0` —— 整件事**全程静默**。
         * 更糟的是它能把回测结论翻号：同一份数据，把赢家改个名排到字母表前面，
         * net_return 从 −2.71% 变成 +3.88%，`passed` 跟着从 false 翻成 true。
         *
         * ⛔ 别再改回逐单累加。额度不够时**按请求名义额等比缩减**，并如实上报。
         */
        if (sym_cap_trimmed > 0.0) {
            symbol_cap_days += 1;
            symbol_cap_trimmed += sym_cap_trimmed;
        }

        double cap_trimmed = 0.0;
        int buys_before_cap = 0;
        for (const auto& o : buys) if (o.quantity > 0) ++buys_before_cap;
        if (risk_mgr.has_total_cap()) {
            double want = 0.0;
            for (const auto& o : buys) {
                if (o.quantity <= 0) continue;
                want += o.quantity * open_of(o.symbol)->open;
            }
            double headroom = std::max(
                0.0, total_value_now * risk_mgr.config().max_total_position_pct - total_pos_now);
            if (want > headroom && want > 0.0) {
                double scale = headroom / want;
                for (auto& o : buys) {
                    if (o.quantity <= 0) continue;
                    int before = o.quantity;
                    o.quantity = std::max(0, static_cast<int>(before * scale / lot) * lot);
                    cap_trimmed += (before - o.quantity) * open_of(o.symbol)->open;
                }
            }
        }
        if (cap_trimmed > 0.0) {
            cap_contention_days += 1;
            cap_contention_trimmed += cap_trimmed;
        }

        // ── (2c) 现金：算成本、看够不够 ──
        double need = 0.0;
        std::vector<double> costs(buys.size(), 0.0);
        for (size_t i = 0; i < buys.size(); ++i) {
            auto& order = buys[i];
            if (order.quantity <= 0) continue;
            const Bar* b = open_of(order.symbol);
            double px = commission_config_.apply_slippage(b->open, true);
            double gross = px * order.quantity;
            costs[i] = gross + commission_config_.calculate(gross, false);
            need += costs[i];
        }

        /*
         * 🔴 资金竞争：请求合计超过账上现金时**按名义额等比缩减**。
         *
         * ⛔ 不用「先到先得」—— 那样谁买得到由 std::map 的字母序决定，
         *    BTC 永远排在 SOL 前面，是个看不见的系统性偏袒。
         * ⛔ 不静默：削了几天、削掉多少，随结果一起返回。
         */
        double cash_now = portfolio.get_cash();
        int effective_buys = 0;
        for (const auto& o : buys) if (o.quantity > 0) ++effective_buys;

        if (need > cash_now && need > 0.0) {
            double scale = cash_now / need;
            double trimmed = 0.0;
            for (size_t i = 0; i < buys.size(); ++i) {
                if (buys[i].quantity <= 0) continue;
                int before = buys[i].quantity;
                int after = static_cast<int>(before * scale / lot) * lot;
                buys[i].quantity = std::max(0, after);
                /*
                 * ⚠️ `trimmed` 按**请求名义额**折算，不是按现金。所以它可能
                 * 大于账户现金（三个标的各请求 90%、账上 1500 → trimmed 3015），
                 * 那不是 bug：削掉的确实是 3015 的**请求**。
                 * ⚠️ 另外这里假设成本对数量线性，而 A 股有最低佣金 5 元，
                 * 小额单缩减后佣金不按比例降 → `trimmed` 会略微高估。
                 */
                trimmed += costs[i] * (before - buys[i].quantity) / static_cast<double>(before);
            }
            /*
             * ⚠️ 只有**真的有两个以上买单在抢**才算一次资金竞争。
             * 判据若只写 `need > cash`，那么单标的请求 100% 现金、手续费一顶就超，
             * 也会被记成「抢钱了」—— 这个字段是给 Jason 看「几天出现过抢钱」的，
             * 对任何满仓请求都报警等于没用。
             */
            if (effective_buys >= 2) {
                cash_contention_days += 1;
                cash_contention_trimmed += trimmed;
            }
        }

        for (auto& order : buys) {
            if (order.quantity <= 0) continue;
            const Bar* b = open_of(order.symbol);
            portfolio.execute_order(order, b->open, today);
        }

        // 今天没 bar 的标的，其挂单顺延到它下一个有 bar 的日子（不是丢掉）
        pending = std::move(carried);

        // ── (3) 用今日收盘价标记持仓市值 ──
        // ⚠️ 只标今天**有 bar** 的标的；没有 bar 的沿用上次价格（Position 里存着）。
        for (auto& [sym, st] : state) {
            auto iit = st.index.find(today);
            if (iit == st.index.end()) continue;
            const Bar& bar = bars_[sym][iit->second];
            portfolio.update_price(sym, bar.close);
            st.history.push_back(bar);
        }

        // ── (4) 逐标的决策 ──
        double total_value_eod = portfolio.get_total_value();
        double market_value_eod = portfolio.get_market_value();
        for (auto& [sym, st] : state) {
            auto iit = st.index.find(today);
            if (iit == st.index.end()) continue;   // 今天这个标的没数据 → 不决策
            const Bar& bar = bars_[sym][iit->second];

            StrategyContext ctx;
            ctx.symbol = sym;
            ctx.bar_index = static_cast<int>(st.history.size()) - 1;
            ctx.current_bar = bar;
            ctx.history = &st.history;
            // ⭐ 每个标的看到的是**当下的共享现金余额**，前面标的花掉的钱这里就没了
            ctx.cash = portfolio.get_cash();
            ctx.position_quantity = portfolio.get_position_quantity(sym);
            ctx.position_avg_price = portfolio.get_position_avg_price(sym);
            ctx.total_value = total_value_eod;
            ctx.market_value = market_value_eod;
            ctx.lot_size = market_rules_.lot_size;

            // (4a) 风控止损（用今日收盘价判定）→ 触发则挂到下一 bar 开盘成交
            auto risk_orders = risk_mgr.check_stop_loss(
                sym, bar.close, ctx.position_quantity,
                ctx.position_avg_price, ctx.total_value);
            if (!risk_orders.empty()) {
                for (auto& order : risk_orders) {
                    order.symbol = sym;
                    pending.push_back(order);
                }
                continue;   // 止损触发的标的，今天不再跑策略
            }

            // (4b) 策略决策
            auto orders = strategy_->on_bar(ctx);
            for (auto& order : orders) {
                // ⚠️ 只在策略没填 symbol 时补默认值。
                // ⛔ 旧版这里是无条件 `order.symbol = symbol_`，把策略设的 symbol
                //    **强行覆盖** —— 于是 PairsStrategy 那种想下第二条腿的策略，
                //    单子会被改成第一只标的，另一条腿从来没真的交易过。
                if (order.symbol.empty()) order.symbol = sym;
                pending.push_back(order);
            }
        }

        // ── (5) 记录今天的净值 ──
        portfolio.record_equity(today);
    }

    // 回测区间最后一天挂起的订单没有"次日开盘"可成交 → 丢弃（现实中同样无法执行）。
    // 不静默：记下数量随结果返回，避免两次仅相差一天的回测因为这批订单消失而报出
    // 看似矛盾的成交数/绩效。
    /*
     * 收尾时 `pending` 里其实混着**两种**订单，原因完全不同，分开数：
     *   ① 最后一天产生的 —— 没有「次日开盘」，现实中同样无法执行；
     *   ② **挂死的** —— 某标的的数据半途就断了，它的挂单一路顺延到结束。
     * 混在一起会让日志说「最后一 bar 有 N 笔被丢弃」这种假话。
     */
    const std::string& last_day = dates.back();
    int dropped_last_bar_orders = 0, dropped_stale_orders = 0;
    for (const auto& o : pending) {
        auto sit = state.find(o.symbol);
        bool had_bar_on_last_day =
            sit != state.end() && sit->second.index.count(last_day) > 0;
        (had_bar_on_last_day ? dropped_last_bar_orders : dropped_stale_orders) += 1;
    }
    if (dropped_last_bar_orders > 0) {
        std::cerr << "[backtest] 回测区间最后一 bar 有 " << dropped_last_bar_orders
                  << " 笔挂单因无次日开盘可成交而被丢弃" << std::endl;
    }
    if (dropped_stale_orders > 0) {
        std::cerr << "[backtest] 另有 " << dropped_stale_orders
                  << " 笔挂单因所属标的的数据半途断了而一直没等到成交" << std::endl;
    }

    // ── Step 5: 策略清理 ──
    strategy_->on_finish();

    // ── Step 6: 计算绩效指标 ──
    auto metrics = Metrics::calculate(
        portfolio.get_equity_curve(),
        portfolio.get_fills(),
        initial_capital_
    );

    // ── Step 7: 组装结果 ──
    BacktestResult result;
    result.symbols = load_order_;
    for (const auto& s : load_order_) {
        if (!result.symbol.empty()) result.symbol += ",";
        result.symbol += s;
    }
    result.strategy_name = strategy_->name();
    result.metrics = metrics;
    result.equity_curve = portfolio.get_equity_curve();
    result.trades = portfolio.get_fills();
    result.dropped_last_bar_orders = dropped_last_bar_orders;
    result.dropped_stale_orders = dropped_stale_orders;
    for (const auto& [sym, st] : state) {
        result.bar_coverage[sym] = static_cast<int>(st.index.size());
    }
    result.cash_contention_days = cash_contention_days;
    result.cash_contention_trimmed = cash_contention_trimmed;
    result.cap_contention_days = cap_contention_days;
    result.cap_contention_trimmed = cap_contention_trimmed;
    result.symbol_cap_days = symbol_cap_days;
    result.symbol_cap_trimmed = symbol_cap_trimmed;
    result.duplicate_dates = duplicate_dates;
    if (duplicate_dates > 0) {
        std::cerr << "[backtest] 输入数据有 " << duplicate_dates
                  << " 根 bar 的日期与同标的另一根重复，每天只保留了最后一根"
                  << std::endl;
    }

    return result;
}

}  // namespace backtest
