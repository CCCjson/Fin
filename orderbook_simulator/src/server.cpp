/*
 * server.cpp — REST API 服务器的实现
 *
 * 这是整个项目对外暴露的"入口"：
 * 外部（前端 / Python 后端）通过 HTTP 请求和这个服务器通信，
 * 服务器把请求转发给 SessionManager / Session 处理，
 * 然后把结果以 JSON 格式返回。
 */

#include "orderbook/server.h"
#include <iostream>   // std::cout 输出到控制台

// 使用 nlohmann::json 的简写别名
using json = nlohmann::json;

namespace orderbook {

Server::Server(int port)
    : port_(port)
{
}

void Server::start() {
    // 注册路由
    setup_routes();

    std::cout << "========================================" << std::endl;
    std::cout << "  订单簿模拟器 (C++) 启动" << std::endl;
    std::cout << "  端口: " << port_ << std::endl;
    std::cout << "  API:  http://localhost:" << port_ << "/api" << std::endl;
    std::cout << "========================================" << std::endl;

    // listen 是阻塞调用，服务器会一直运行
    // "0.0.0.0" 表示监听所有网络接口（本机 + 局域网都能访问）
    svr_.listen("0.0.0.0", port_);
}

void Server::stop() {
    svr_.stop();
}


// ============================================================
// 路由注册
// ============================================================

void Server::setup_routes() {
    /*
     * cpp-httplib 的路由注册方式：
     *   svr_.Get("/path", handler_function);
     *   svr_.Post("/path", handler_function);
     *
     * 路径参数用正则表达式捕获：
     *   "/api/sessions/([^/]+)/depth"
     *   ([^/]+) 匹配"非斜杠的一个或多个字符"，即 session_id
     *   匹配结果在 req.matches[1] 中
     *
     * lambda 函数：
     *   [this](args) { body }
     *   [this] 表示捕获 this 指针，这样 lambda 内部可以访问成员变量
     */

    // ── 健康检查 ──
    svr_.Get("/health", [](const httplib::Request&, httplib::Response& res) {
        res.set_content(R"({"status":"healthy","service":"orderbook_simulator"})",
                        "application/json");
    });

    // ── POST /api/sessions — 创建会话 ──
    svr_.Post("/api/sessions",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_create_session(req, res);
        }
    );

    // ── GET /api/sessions/:id/depth ──
    svr_.Get(R"(/api/sessions/([^/]+)/depth)",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_get_depth(req, res);
        }
    );

    // ── POST /api/sessions/:id/orders ──
    svr_.Post(R"(/api/sessions/([^/]+)/orders)",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_submit_order(req, res);
        }
    );

    // ── DELETE /api/sessions/:id/orders/:oid ──
    svr_.Delete(R"(/api/sessions/([^/]+)/orders/([^/]+))",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_cancel_order(req, res);
        }
    );

    // ── GET /api/sessions/:id/fills ──
    svr_.Get(R"(/api/sessions/([^/]+)/fills)",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_get_fills(req, res);
        }
    );

    // ── GET /api/sessions/:id/stats ──
    svr_.Get(R"(/api/sessions/([^/]+)/stats)",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_get_stats(req, res);
        }
    );

    // ── POST /api/sessions/:id/seed ──
    svr_.Post(R"(/api/sessions/([^/]+)/seed)",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_seed_orders(req, res);
        }
    );

    // ── CORS 预检请求 (OPTIONS) ──
    // 浏览器在发送跨域请求前会先发一个 OPTIONS 请求
    // 我们需要回复允许跨域的头信息
    svr_.Options(R"(.*)", [](const httplib::Request&, httplib::Response& res) {
        res.set_header("Access-Control-Allow-Origin", "*");
        res.set_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type");
        res.status = 204;   // 204 No Content
    });

    // ── 全局 CORS 头 ──
    // 给每个响应都加上 CORS 头，允许跨域访问
    svr_.set_post_routing_handler(
        [](const httplib::Request&, httplib::Response& res) {
            res.set_header("Access-Control-Allow-Origin", "*");
        }
    );
}


// ============================================================
// 路由处理函数
// ============================================================

void Server::handle_create_session(const httplib::Request& req, httplib::Response& res) {
    // 解析请求体 JSON
    json body;
    try {
        body = json::parse(req.body);
    } catch (...) {
        // ... 是"捕获所有异常"，相当于 Python 的 except Exception
        error_response(res, "Invalid JSON body");
        return;
    }

    // 从 JSON 中取值，提供默认值
    std::string symbol = body.value("symbol", "TEST");
    // .value("key", default) 如果 key 存在就用它的值，不存在就用 default

    double mid_price = body.value("mid_price", 100.0);
    int seed_count = body.value("seed_count", 200);
    double tick_size = body.value("tick_size", 0.01);
    int spread_ticks = body.value("spread_ticks", 2);

    // 创建会话
    std::string sid = manager_.create_session(symbol);
    Session* session = manager_.get_session(sid);

    // 播种初始订单
    if (session && seed_count > 0) {
        session->seed_orders(seed_count, mid_price, tick_size, spread_ticks);
    }

    // 返回结果
    json result = {
        {"session_id", sid},
        {"symbol", symbol},
        {"seed_count", seed_count},
        {"message", "Session created successfully"}
    };
    json_response(res, result, 201);   // 201 Created
}


void Server::handle_get_depth(const httplib::Request& req, httplib::Response& res) {
    // req.matches[1] 获取路径中的第一个正则捕获组（session_id）
    std::string sid = req.matches[1];

    Session* session = manager_.get_session(sid);
    if (!session) {
        error_response(res, "Session not found", 404);
        return;
    }

    // 从查询参数中获取 levels（默认 10）
    int levels = 10;
    if (req.has_param("levels")) {
        // std::stoi = string to int
        try {
            levels = std::stoi(req.get_param_value("levels"));
        } catch (...) {
            // 解析失败就用默认值
        }
    }

    auto depth = session->get_depth(levels);
    json_response(res, depth.to_json());
}


void Server::handle_submit_order(const httplib::Request& req, httplib::Response& res) {
    std::string sid = req.matches[1];

    Session* session = manager_.get_session(sid);
    if (!session) {
        error_response(res, "Session not found", 404);
        return;
    }

    // 解析订单参数
    json body;
    try {
        body = json::parse(req.body);
    } catch (...) {
        error_response(res, "Invalid JSON body");
        return;
    }

    // 构建订单对象
    BookOrder order;
    order.order_id = generate_id();
    order.side = string_to_side(body.value("side", "BUY"));
    order.order_type = string_to_order_type(body.value("order_type", "LIMIT"));
    order.price = body.value("price", 0.0);
    order.quantity = body.value("quantity", 100);
    order.timestamp = now_ns();
    order.client_tag = "user";   // 标记为用户订单

    // 提交撮合
    auto result = session->submit_order(std::move(order));

    // 返回结果
    json_response(res, result.to_json());
}


void Server::handle_cancel_order(const httplib::Request& req, httplib::Response& res) {
    std::string sid = req.matches[1];
    std::string oid = req.matches[2];   // 第二个捕获组 = order_id

    Session* session = manager_.get_session(sid);
    if (!session) {
        error_response(res, "Session not found", 404);
        return;
    }

    bool success = session->cancel_order(oid);

    json result = {
        {"success", success},
        {"order_id", oid},
        {"message", success ? "Order cancelled" : "Order not found"}
    };
    json_response(res, result, success ? 200 : 404);
}


void Server::handle_get_fills(const httplib::Request& req, httplib::Response& res) {
    std::string sid = req.matches[1];

    Session* session = manager_.get_session(sid);
    if (!session) {
        error_response(res, "Session not found", 404);
        return;
    }

    int limit = 50;
    if (req.has_param("limit")) {
        try {
            limit = std::stoi(req.get_param_value("limit"));
        } catch (...) {}
    }

    auto fills = session->get_recent_fills(limit);

    json result;
    result["fills"] = json::array();
    for (const auto& fill : fills) {
        result["fills"].push_back(fill.to_json());
    }
    result["total"] = static_cast<int>(session->get_fills().size());

    json_response(res, result);
}


void Server::handle_get_stats(const httplib::Request& req, httplib::Response& res) {
    std::string sid = req.matches[1];

    Session* session = manager_.get_session(sid);
    if (!session) {
        error_response(res, "Session not found", 404);
        return;
    }

    auto stats = session->get_stats();
    json_response(res, stats.to_json());
}


void Server::handle_seed_orders(const httplib::Request& req, httplib::Response& res) {
    std::string sid = req.matches[1];

    Session* session = manager_.get_session(sid);
    if (!session) {
        error_response(res, "Session not found", 404);
        return;
    }

    json body;
    try {
        body = json::parse(req.body);
    } catch (...) {
        error_response(res, "Invalid JSON body");
        return;
    }

    int count = body.value("count", 100);
    double mid_price = body.value("mid_price", 100.0);
    double tick_size = body.value("tick_size", 0.01);
    int spread_ticks = body.value("spread_ticks", 2);

    int added = session->seed_orders(count, mid_price, tick_size, spread_ticks);

    json result = {
        {"added_count", added},
        {"message", "Orders seeded successfully"}
    };
    json_response(res, result);
}


// ============================================================
// 工具函数
// ============================================================

void Server::json_response(httplib::Response& res, const json& body, int status) {
    res.status = status;
    res.set_content(body.dump(), "application/json");
    // dump() 把 json 对象序列化为字符串
    // "application/json" 是 HTTP Content-Type 头，告诉客户端"返回的是 JSON"
}

void Server::error_response(httplib::Response& res, const std::string& message, int status) {
    json body = {
        {"error", message},
        {"success", false}
    };
    json_response(res, body, status);
}

}  // namespace orderbook
