/*
 * server.cpp — REST API 服务器的实现
 *
 * 使用 cpp-httplib 提供 HTTP 服务。
 * 所有端点接受 JSON 请求，返回 JSON 响应。
 *
 * 知识点：
 * - lambda 表达式 [&](Request& req, Response& res) { ... }
 *   [&] 表示捕获外部所有变量的引用。
 *   lambda 就是"匿名函数"，可以在需要的地方直接定义函数。
 *
 * - httplib::Server 的工作方式：
 *   1. 注册路由（URL 路径 + 处理函数）
 *   2. 调用 listen() 启动服务器
 *   3. 每当有请求进来，httplib 匹配路由，调用对应的处理函数
 */

#include "backtest/server.h"
#include "backtest/engine.h"
#include "backtest/data_loader.h"
#include "backtest/types.h"

/* ─ 策略头文件 ─ */
#include "strategies/ma_cross_strategy.h"
#include "strategies/momentum_strategy.h"
#include "strategies/macd_strategy.h"
#include "strategies/rsi_strategy.h"
#include "strategies/kdj_strategy.h"
#include "strategies/bollinger_strategy.h"
#include "strategies/combo_strategy.h"
#include "strategies/pairs_strategy.h"
#include "strategies/external_signal_strategy.h"
#include "strategies/portfolio_signal_strategy.h"

#include <httplib.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <memory>      // make_unique
#include <cctype>      // std::tolower
#include <map>
#include <set>

using json = nlohmann::json;

namespace backtest {

/*
 * 全局 httplib::Server 实例
 * 放在匿名命名空间中，限制作用域在本文件内。
 *
 * 匿名命名空间（anonymous namespace）：
 * 里面的东西只能在本 .cpp 文件中使用，其他文件看不到。
 * 类似于 static 全局变量的效果，但更 C++ 风格。
 */
namespace {
    httplib::Server svr;
}

Server::Server(int port) : port_(port) {}

void Server::stop() {
    svr.stop();
}

/*
 * 创建策略的工厂函数
 *
 * 根据策略名称字符串创建对应的策略对象。
 * 返回 unique_ptr<IStrategy>：多态智能指针。
 *
 * make_unique<MACrossStrategy>(...)：
 * 创建一个 MACrossStrategy 对象，并用 unique_ptr 包装。
 * 这比 new + unique_ptr 更安全（异常安全）。
 */
static std::unique_ptr<IStrategy> create_strategy(
    const std::string& name,
    const json& params
) {
    if (name == "MA_CROSS") {
        int fast = params.value("fast_period", 5);
        int slow = params.value("slow_period", 20);
        double pct = params.value("position_pct", 0.95);
        return std::make_unique<MACrossStrategy>(fast, slow, pct);
    }
    else if (name == "MOMENTUM") {
        int lookback = params.value("lookback", 20);
        double buy_th = params.value("buy_threshold", 0.05);
        double sell_th = params.value("sell_threshold", -0.03);
        double pct = params.value("position_pct", 0.95);
        return std::make_unique<MomentumStrategy>(lookback, buy_th, sell_th, pct);
    }
    else if (name == "MACD") {
        int fast = params.value("fast_period", 12);
        int slow = params.value("slow_period", 26);
        int signal = params.value("signal_period", 9);
        double pct = params.value("position_pct", 0.95);
        return std::make_unique<MACDStrategy>(fast, slow, signal, pct);
    }
    else if (name == "RSI") {
        int period = params.value("period", 14);
        double oversold = params.value("oversold", 30.0);
        double overbought = params.value("overbought", 70.0);
        double pct = params.value("position_pct", 0.95);
        return std::make_unique<RSIStrategy>(period, oversold, overbought, pct);
    }
    else if (name == "KDJ") {
        int n = params.value("n", 9);
        int m1 = params.value("m1", 3);
        int m2 = params.value("m2", 3);
        double oversold = params.value("oversold", 20.0);
        double overbought = params.value("overbought", 80.0);
        double pct = params.value("position_pct", 0.95);
        return std::make_unique<KDJStrategy>(n, m1, m2, oversold, overbought, pct);
    }
    else if (name == "BOLLINGER") {
        int period = params.value("period", 20);
        double num_std = params.value("num_std", 2.0);
        double pct = params.value("position_pct", 0.95);
        return std::make_unique<BollingerStrategy>(period, num_std, pct);
    }
    else if (name == "COMBO") {
        double threshold = params.value("threshold", 0.5);
        double pct = params.value("position_pct", 0.95);
        auto combo = std::make_unique<ComboStrategy>(threshold, pct);

        if (params.contains("sub_strategies") && params["sub_strategies"].is_array()) {
            for (const auto& sub : params["sub_strategies"]) {
                std::string sub_name = sub.value("name", "");
                double weight = sub.value("weight", 1.0);
                json sub_params = sub.value("params", json::object());
                auto sub_strategy = create_strategy(sub_name, sub_params);
                if (sub_strategy) {
                    combo->add_sub_strategy(sub_name, weight, std::move(sub_strategy));
                }
            }
        }
        return combo;
    }
    else if (name == "PAIRS") {
        std::string symbol2 = params.value("symbol2", "");
        int lookback = params.value("lookback", 60);
        double entry_z = params.value("entry_z", 2.0);
        double exit_z = params.value("exit_z", 0.5);
        double pct = params.value("position_pct", 0.95);

        // bars2 从 params 中提取
        std::vector<Bar> bars2;
        if (params.contains("bars2") && params["bars2"].is_array()) {
            bars2 = DataLoader::from_json(params["bars2"]);
        }
        return std::make_unique<PairsStrategy>(symbol2, bars2, lookback, entry_z, exit_z, pct);
    }

    return nullptr;   // 未知策略
}

/*
 * 把 BacktestResult 转为 JSON
 */
static json result_to_json(const BacktestResult& result) {
    json j;
    j["symbol"] = result.symbol;
    j["strategy_name"] = result.strategy_name;

    // 绩效指标
    auto& m = result.metrics;
    j["metrics"] = {
        {"total_return", m.total_return},
        {"annualized_return", m.annualized_return},
        {"final_value", m.final_value},
        {"volatility", m.volatility},
        {"max_drawdown", m.max_drawdown},
        {"max_drawdown_amount", m.max_drawdown_amount},
        {"max_dd_start_date", m.max_dd_start_date},
        {"max_dd_end_date", m.max_dd_end_date},
        {"sharpe_ratio", m.sharpe_ratio},
        {"sortino_ratio", m.sortino_ratio},
        {"total_trades", m.total_trades},
        {"winning_trades", m.winning_trades},
        {"losing_trades", m.losing_trades},
        {"win_rate", m.win_rate},
        {"profit_factor", m.profit_factor},
        {"avg_profit", m.avg_profit},
        {"avg_loss", m.avg_loss},
        {"total_commission", m.total_commission},
        {"total_slippage", m.total_slippage}
    };

    // 资金曲线
    json eq = json::array();
    for (const auto& snap : result.equity_curve) {
        eq.push_back({
            {"date", snap.date},
            {"cash", snap.cash},
            {"market_value", snap.market_value},
            {"total_value", snap.total_value},
            {"daily_return", snap.daily_return}
        });
    }
    j["equity_curve"] = eq;

    // 成交记录
    json trades = json::array();
    for (const auto& f : result.trades) {
        trades.push_back({
            {"order_id", f.order_id},
            {"symbol", f.symbol},
            {"side", side_to_string(f.side)},
            {"price", f.price},
            {"quantity", f.quantity},
            {"commission", f.commission},
            {"slippage", f.slippage},
            {"date", f.date},
            {"reason", f.reason}
        });
    }
    j["trades"] = trades;

    // 回测最后一 bar 因无「次日开盘」可成交而被丢弃的挂单数（不静默，供前端/日志核对）
    j["dropped_last_bar_orders"] = result.dropped_last_bar_orders;

    // ── 组合回测（S8）：这三个字段单标的回测也会有，只是值退化 ──
    j["symbols"] = result.symbols;
    // 每个标的实际有多少天有 bar —— ⛔ 缺 bar ≠ 数据是 0，覆盖天数必须能被看见
    j["bar_coverage"] = result.bar_coverage;
    // 资金竞争：同一天多个买单抢同一份现金，按名义额等比缩减了多少
    j["cash_contention"] = {
        {"days", result.cash_contention_days},
        {"trimmed_notional", result.cash_contention_trimmed}
    };
    // 输入里有多少根 bar 的日期与同标的另一根重复（>0 说明喂进来的数据坏了）
    j["duplicate_dates"] = result.duplicate_dates;
    // 某标的数据半途断了、挂单一路顺延到结束的数量（与「最后一 bar 丢弃」分开计）
    j["dropped_stale_orders"] = result.dropped_stale_orders;

    /*
     * ⚠️ **引擎口径版本**。S8 改了两件会直接改变数字的事：
     *   ① crypto 的一手从 100 股变成 1 股（最小成交额 ~$1000 → ~$10）
     *   ② 仓位上限现在把已有持仓算进去，且不再被 `risk_config.enabled` 关掉
     * 拿 v1 时代的回测数字跟现在的比大小是**没有意义**的（S3 竞技场的
     * 四道门槛读的就是这些数）。所以把版本随结果一起带出来，别让两代数字混着看。
     */
    j["engine_version"] = "cpp-backtest-v2-portfolio";

    return j;
}

/*
 * 按市场取交易单位规则。
 * ⚠️ 与 CommissionConfig 的市场分支**必须同步**，别一处加了市场另一处忘了。
 */
static MarketRules market_rules_of(const std::string& market) {
    if (market == "us")     return MarketRules::us_stock();
    if (market == "hk")     return MarketRules::hk_stock();
    if (market == "crypto") return MarketRules::crypto();
    return MarketRules::a_share();
}

static CommissionConfig commission_of(const std::string& market) {
    if (market == "us")     return CommissionConfig::us_stock();
    if (market == "hk")     return CommissionConfig::hk_stock();
    if (market == "crypto") return CommissionConfig::crypto();
    return CommissionConfig::a_share();
}

static RiskConfig risk_from_json(const json& body) {
    RiskConfig risk_cfg;
    if (body.contains("risk_config")) {
        auto rc = body["risk_config"];
        risk_cfg.enabled = rc.value("enabled", false);
        risk_cfg.stop_loss_pct = rc.value("stop_loss_pct", 0.05);
        risk_cfg.trailing_stop = rc.value("trailing_stop", false);
        risk_cfg.trailing_stop_pct = rc.value("trailing_stop_pct", 0.08);
        risk_cfg.max_position_pct = rc.value("max_position_pct", 1.0);
        // 🔒 总仓位上限（0.8 = 必须留 20% 现金）。与单标的上限是**两条独立约束**，
        //    引擎里取更严的那个 —— ⛔ 绝不是 max，见 risk_manager.h 的说明。
        risk_cfg.max_total_position_pct = rc.value("max_total_position_pct", 1.0);
    }
    return risk_cfg;
}

/* 把 [{date, action, price?, weight?}] 解析成 date → SignalEntry（同日后者覆盖前者）*/
static std::map<std::string, SignalEntry> parse_signals(const json& arr) {
    std::map<std::string, SignalEntry> signals;
    if (!arr.is_array()) return signals;
    for (const auto& s : arr) {
        std::string date = s.value("date", "");
        std::string action = s.value("action", "");
        for (auto& c : action) c = static_cast<char>(std::tolower(c));
        if (date.empty() || (action != "buy" && action != "sell")) {
            continue;   // 跳过非法条目
        }
        SignalEntry entry;
        entry.side = (action == "buy") ? Side::BUY : Side::SELL;
        entry.weight = s.value("weight", 0.95);
        if (s.contains("price") && s["price"].is_number()) {
            entry.price = s["price"].get<double>();
            entry.has_price = true;
        }
        signals[date] = entry;
    }
    return signals;
}

void Server::setup_routes() {
    /*
     * CORS 设置
     * 允许浏览器的跨域请求。
     * 在开发环境中，前端 (localhost:5173) 和后端 (localhost:8002)
     * 不同端口算跨域，需要设置 CORS 头。
     */
    svr.set_default_headers({
        {"Access-Control-Allow-Origin", "*"},
        {"Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"},
        {"Access-Control-Allow-Headers", "Content-Type"}
    });

    // OPTIONS 请求（CORS 预检）
    svr.Options(".*", [](const httplib::Request&, httplib::Response& res) {
        res.status = 204;
    });

    // ── GET /api/strategies — 获取可用策略列表 ──
    svr.Get("/api/strategies", [](const httplib::Request&, httplib::Response& res) {
        json strategies = json::array();

        // MA_CROSS
        {
            MACrossStrategy s;
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        // MOMENTUM
        {
            MomentumStrategy s;
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        // MACD
        {
            MACDStrategy s;
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        // RSI
        {
            RSIStrategy s;
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        // KDJ
        {
            KDJStrategy s;
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        // BOLLINGER
        {
            BollingerStrategy s;
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        // COMBO
        {
            ComboStrategy s;
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        // PAIRS
        {
            PairsStrategy s("", {});
            strategies.push_back({
                {"name", s.name()},
                {"description", s.description()},
                {"params", s.param_schema()}
            });
        }

        res.set_content(strategies.dump(), "application/json");
    });

    // ── POST /api/backtest/run — 运行回测 ──
    /*
     * 请求格式：
     * {
     *   "symbol": "AAPL",
     *   "strategy": "MA_CROSS",
     *   "params": { "fast_period": 5, "slow_period": 20 },
     *   "bars": [ {"date": "...", "open": ..., ...}, ... ],
     *   "initial_capital": 100000,
     *   "market": "us",
     *   "start_date": "2025-01-01",
     *   "end_date": "2025-12-31"
     * }
     */
    svr.Post("/api/backtest/run", [](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);

            // 解析参数
            std::string symbol = body.value("symbol", "TEST");
            std::string strategy_name = body.value("strategy", "MA_CROSS");
            json params = body.value("params", json::object());
            double initial_capital = body.value("initial_capital", 100000.0);
            std::string market = body.value("market", "a_share");
            std::string start_date = body.value("start_date", "");
            std::string end_date = body.value("end_date", "");

            // 加载数据
            std::vector<Bar> bars;
            if (body.contains("bars") && body["bars"].is_array()) {
                bars = DataLoader::from_json(body["bars"]);
            } else {
                // 没有提供数据，生成模拟数据（用于测试）
                bars = DataLoader::generate_sample_data(252);
            }

            if (bars.empty()) {
                res.status = 400;
                res.set_content(json({{"error", "No data provided or generated"}}).dump(),
                                "application/json");
                return;
            }

            // ⚠️ 三个端点共用同一套解析（`commission_of`/`risk_from_json`/
            // `market_rules_of`）。此前这里是自己抄的一份 inline 逻辑，结果是
            // **本端点读不到 `max_total_position_pct`、也拿不到 MarketRules**
            // —— market=crypto 时一手仍然是 100 股，20% 现金保护配了也不生效。
            CommissionConfig comm = commission_of(market);
            double custom_slippage = body.value("slippage_pct", -1.0);   // -1 = 用市场默认
            if (custom_slippage >= 0.0) {
                comm.slippage_pct = custom_slippage;
            }
            RiskConfig risk_cfg = risk_from_json(body);

            // 创建策略
            auto strategy = create_strategy(strategy_name, params);
            if (!strategy) {
                res.status = 400;
                res.set_content(
                    json({{"error", "Unknown strategy: " + strategy_name}}).dump(),
                    "application/json");
                return;
            }

            // 运行回测
            BacktestEngine engine(initial_capital, comm, risk_cfg, market_rules_of(market));
            engine.set_strategy(std::move(strategy));
            engine.load_data(symbol, std::move(bars));

            auto result = engine.run(start_date, end_date);

            // 返回结果
            res.set_content(result_to_json(result).dump(), "application/json");

        } catch (const EmptyDateRange& e) {
            // ⚠️ 400 不是 500：调用方给的窗口里一根 bar 都没有，是**输入**问题。
            // ⛔ 旧引擎遇到这种情况静默跑全量数据并回 200 —— 等于对调用方撒谎。
            res.status = 400;
            res.set_content(
                json({{"error", std::string(e.what())}}).dump(),
                "application/json");
        } catch (const std::exception& e) {
            res.status = 500;
            res.set_content(
                json({{"error", std::string(e.what())}}).dump(),
                "application/json");
        }
    });

    // ── POST /api/backtest/run_signals — 外部信号驱动回测 ──
    /*
     * 请求格式（与 /run 相同，但用 signals 取代 strategy/params）：
     * {
     *   "symbol": "AAPL",
     *   "signals": [ {"date": "2025-01-15", "action": "buy", "weight": 0.9, "price": 12.3}, ... ],
     *   "bars": [ {"date": "...", "open": ..., ...}, ... ],   // 必填
     *   "initial_capital": 100000,
     *   "market": "us" | "hk" | "a_share",
     *   "slippage_pct": 0.001,     // 可选
     *   "risk_config": { ... },    // 可选
     *   "start_date": "...", "end_date": "..."   // 可选
     * }
     * action 大小写不敏感；weight 缺省 0.95；price 缺省=市价单。
     * 成交与 /run 一致：信号日次日开盘成交（防未来函数）、T+1、滑点、费用。
     */
    svr.Post("/api/backtest/run_signals", [](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);

            std::string symbol = body.value("symbol", "TEST");
            double initial_capital = body.value("initial_capital", 100000.0);
            std::string market = body.value("market", "a_share");
            std::string start_date = body.value("start_date", "");
            std::string end_date = body.value("end_date", "");

            // 加载 K 线（信号回测必须显式提供 bars，不生成模拟数据）
            std::vector<Bar> bars;
            if (body.contains("bars") && body["bars"].is_array()) {
                bars = DataLoader::from_json(body["bars"]);
            }
            if (bars.empty()) {
                res.status = 400;
                res.set_content(json({{"error", "run_signals requires non-empty 'bars'"}}).dump(),
                                "application/json");
                return;
            }

            // 解析信号序列 → date→SignalEntry（同一天后者覆盖前者）
            std::map<std::string, SignalEntry> signals =
                body.contains("signals") ? parse_signals(body["signals"])
                                         : std::map<std::string, SignalEntry>{};

            CommissionConfig comm = commission_of(market);
            double custom_slippage = body.value("slippage_pct", -1.0);
            if (custom_slippage >= 0.0) {
                comm.slippage_pct = custom_slippage;
            }
            RiskConfig risk_cfg = risk_from_json(body);

            // 运行回测（策略换成信号回放，引擎/撮合/指标全复用）
            BacktestEngine engine(initial_capital, comm, risk_cfg, market_rules_of(market));
            engine.set_strategy(std::make_unique<ExternalSignalStrategy>(std::move(signals)));
            engine.load_data(symbol, std::move(bars));

            auto result = engine.run(start_date, end_date);

            res.set_content(result_to_json(result).dump(), "application/json");

        } catch (const EmptyDateRange& e) {
            // ⚠️ 400 不是 500：调用方给的窗口里一根 bar 都没有，是**输入**问题。
            // ⛔ 旧引擎遇到这种情况静默跑全量数据并回 200 —— 等于对调用方撒谎。
            res.status = 400;
            res.set_content(
                json({{"error", std::string(e.what())}}).dump(),
                "application/json");
        } catch (const std::exception& e) {
            res.status = 500;
            res.set_content(
                json({{"error", std::string(e.what())}}).dump(),
                "application/json");
        }
    });

    /*
     * ── POST /api/backtest/run_portfolio — 组合回测（S8）──
     *
     * 与 /run_signals 的区别只有一个，但那一个是本质的：
     * **N 个标的共享同一份现金**，而不是各发一份完整本金独立跑再把收益率平均。
     * 没有共享资金池就没有资金竞争，带权重的组合策略回测出来的数字
     * 跟权重毫无关系。
     *
     * 请求体：
     * {
     *   "legs": [{"symbol": "BTCUSDT.BN", "bars": [...], "signals": [...]}, ...],
     *   "initial_capital": 100000, "market": "crypto",
     *   "risk_config": {"enabled": true, "max_total_position_pct": 0.8, ...},
     *   "slippage_pct": 0.0005, "start_date": "", "end_date": ""
     * }
     *
     * ⚠️ 某个 leg 只给 bars 不给 signals 是合法的 —— 那个标的只当行情背景
     *    （比如配对交易的另一条腿），不产生订单。
     */
    svr.Post("/api/backtest/run_portfolio", [](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);

            double initial_capital = body.value("initial_capital", 100000.0);
            std::string market = body.value("market", "a_share");
            std::string start_date = body.value("start_date", "");
            std::string end_date = body.value("end_date", "");

            if (!body.contains("legs") || !body["legs"].is_array() || body["legs"].empty()) {
                res.status = 400;
                res.set_content(
                    json({{"error", "run_portfolio requires a non-empty 'legs' array"}}).dump(),
                    "application/json");
                return;
            }

            CommissionConfig comm = commission_of(market);
            double custom_slippage = body.value("slippage_pct", -1.0);
            if (custom_slippage >= 0.0) {
                comm.slippage_pct = custom_slippage;
            }
            RiskConfig risk_cfg = risk_from_json(body);

            BacktestEngine engine(initial_capital, comm, risk_cfg, market_rules_of(market));

            std::map<std::string, std::map<std::string, SignalEntry>> by_symbol;
            std::set<std::string> seen_symbols;
            int loaded = 0;
            for (const auto& leg : body["legs"]) {
                std::string sym = leg.value("symbol", "");
                if (sym.empty()) continue;
                // ⛔ 重复 symbol 直接拒绝：`load_data` 是后者覆盖前者，
                //    静默塌成一条腿会让「10 个币的组合」悄悄变成 3 个币，
                //    而收益率看上去一切正常（与下面拒绝空 bars 是同一类事故）。
                if (by_symbol.count(sym) || seen_symbols.count(sym)) {
                    res.status = 400;
                    res.set_content(
                        json({{"error", "duplicate leg symbol '" + sym + "'"}}).dump(),
                        "application/json");
                    return;
                }
                seen_symbols.insert(sym);
                std::vector<Bar> bars;
                if (leg.contains("bars") && leg["bars"].is_array()) {
                    bars = DataLoader::from_json(leg["bars"]);
                }
                // ⛔ 空 bars 的 leg 直接拒绝，不静默跳过：
                //    静默跳过会让「10 个币的组合」悄悄变成 3 个币的组合，
                //    而返回里的收益率看上去一切正常。
                if (bars.empty()) {
                    res.status = 400;
                    res.set_content(
                        json({{"error", "leg '" + sym + "' has no bars"}}).dump(),
                        "application/json");
                    return;
                }
                if (leg.contains("signals")) {
                    // ⛔ 给了 signals 但不是数组 = 调用方写错了，别静默把这条腿
                    //    退化成「纯行情背景」——那样它一单不下而返回 200。
                    if (!leg["signals"].is_array()) {
                        res.status = 400;
                        res.set_content(
                            json({{"error", "leg '" + sym + "': 'signals' must be an array"}})
                                .dump(), "application/json");
                        return;
                    }
                    by_symbol[sym] = parse_signals(leg["signals"]);
                }
                engine.load_data(sym, std::move(bars));
                ++loaded;
            }
            if (loaded == 0) {
                res.status = 400;
                res.set_content(
                    json({{"error", "no valid legs (every leg needs a 'symbol')"}}).dump(),
                    "application/json");
                return;
            }

            engine.set_strategy(
                std::make_unique<PortfolioSignalStrategy>(std::move(by_symbol)));

            auto result = engine.run(start_date, end_date);
            res.set_content(result_to_json(result).dump(), "application/json");

        } catch (const EmptyDateRange& e) {
            // ⚠️ 400 不是 500：调用方给的窗口里一根 bar 都没有，是**输入**问题。
            // ⛔ 旧引擎遇到这种情况静默跑全量数据并回 200 —— 等于对调用方撒谎。
            res.status = 400;
            res.set_content(
                json({{"error", std::string(e.what())}}).dump(),
                "application/json");
        } catch (const std::exception& e) {
            res.status = 500;
            res.set_content(
                json({{"error", std::string(e.what())}}).dump(),
                "application/json");
        }
    });
}

void Server::start() {
    setup_routes();

    std::cout << "========================================" << std::endl;
    std::cout << "  C++ Backtest Server" << std::endl;
    std::cout << "  Port: " << port_ << std::endl;
    std::cout << "  API: http://localhost:" << port_ << "/api/strategies" << std::endl;
    std::cout << "========================================" << std::endl;

    svr.listen("0.0.0.0", port_);
}

}  // namespace backtest
