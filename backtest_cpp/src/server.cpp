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

#include <httplib.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <memory>      // make_unique

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

    return j;
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

            // 手续费配置
            CommissionConfig comm;
            if (market == "us") {
                comm = CommissionConfig::us_stock();
            } else if (market == "hk") {
                comm = CommissionConfig::hk_stock();
            } else {
                comm = CommissionConfig::a_share();
            }

            // 自定义滑点（-1 表示用市场默认值）
            double custom_slippage = body.value("slippage_pct", -1.0);
            if (custom_slippage >= 0.0) {
                comm.slippage_pct = custom_slippage;
            }

            // 风控配置
            RiskConfig risk_cfg;
            if (body.contains("risk_config")) {
                auto rc = body["risk_config"];
                risk_cfg.enabled = rc.value("enabled", false);
                risk_cfg.stop_loss_pct = rc.value("stop_loss_pct", 0.05);
                risk_cfg.trailing_stop = rc.value("trailing_stop", false);
                risk_cfg.trailing_stop_pct = rc.value("trailing_stop_pct", 0.08);
                risk_cfg.max_position_pct = rc.value("max_position_pct", 1.0);
            }

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
            BacktestEngine engine(initial_capital, comm, risk_cfg);
            engine.set_strategy(std::move(strategy));
            engine.load_data(symbol, std::move(bars));

            auto result = engine.run(start_date, end_date);

            // 返回结果
            res.set_content(result_to_json(result).dump(), "application/json");

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
