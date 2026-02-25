#include "pipeline/server.h"
#include <iostream>

using json = nlohmann::json;

namespace pipeline {

Server::Server(int port, const std::string& db_path)
    : port_(port), orchestrator_(db_path) {}

void Server::start() {
    setup_routes();

    std::cout << "========================================" << std::endl;
    std::cout << "  数据管道服务 (C++) 启动" << std::endl;
    std::cout << "  端口: " << port_ << std::endl;
    std::cout << "  API:  http://localhost:" << port_ << "/api/pipeline" << std::endl;
    std::cout << "========================================" << std::endl;

    svr_.listen("0.0.0.0", port_);
}

void Server::stop() {
    svr_.stop();
}

void Server::setup_routes() {
    // 健康检查
    svr_.Get("/health", [](const httplib::Request&, httplib::Response& res) {
        res.set_content(R"({"status":"healthy","service":"data_pipeline"})", "application/json");
    });

    // POST /api/pipeline/fetch — 提交抓取任务
    svr_.Post("/api/pipeline/fetch",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_fetch(req, res);
        }
    );

    // GET /api/pipeline/status/:task_id — 查询任务进度
    svr_.Get(R"(/api/pipeline/status/([^/]+))",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_status(req, res);
        }
    );

    // POST /api/pipeline/stop/:task_id — 停止任务
    svr_.Post(R"(/api/pipeline/stop/([^/]+))",
        [this](const httplib::Request& req, httplib::Response& res) {
            handle_stop(req, res);
        }
    );

    // GET /api/pipeline/stats — 全局吞吐量统计
    svr_.Get("/api/pipeline/stats",
        [this](const httplib::Request&, httplib::Response& res) {
            handle_stats(httplib::Request(), res);
        }
    );

    // CORS 预检
    svr_.Options(R"(.*)", [](const httplib::Request&, httplib::Response& res) {
        res.set_header("Access-Control-Allow-Origin", "*");
        res.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type");
        res.status = 204;
    });

    // 全局 CORS 头
    svr_.set_post_routing_handler(
        [](const httplib::Request&, httplib::Response& res) {
            res.set_header("Access-Control-Allow-Origin", "*");
        }
    );
}

void Server::handle_fetch(const httplib::Request& req, httplib::Response& res) {
    json body;
    try {
        body = json::parse(req.body);
    } catch (...) {
        error_response(res, "Invalid JSON body");
        return;
    }

    auto fetch_req = FetchRequest::from_json(body);
    std::string task_id = orchestrator_.submit_task(fetch_req);

    json result = {
        {"task_id", task_id},
        {"message", "Task submitted"},
        {"success", true}
    };
    json_response(res, result, 201);
}

void Server::handle_status(const httplib::Request& req, httplib::Response& res) {
    std::string task_id = req.matches[1];
    auto progress = orchestrator_.get_progress(task_id);
    json_response(res, progress.to_json());
}

void Server::handle_stop(const httplib::Request& req, httplib::Response& res) {
    std::string task_id = req.matches[1];
    bool stopped = orchestrator_.stop_task(task_id);

    json result = {
        {"task_id", task_id},
        {"stopped", stopped},
        {"message", stopped ? "Task stop requested" : "Task not found"}
    };
    json_response(res, result, stopped ? 200 : 404);
}

void Server::handle_stats(const httplib::Request&, httplib::Response& res) {
    json_response(res, orchestrator_.get_stats());
}

void Server::json_response(httplib::Response& res, const json& body, int status) {
    res.status = status;
    res.set_content(body.dump(), "application/json");
}

void Server::error_response(httplib::Response& res, const std::string& message, int status) {
    json body = {{"error", message}, {"success", false}};
    json_response(res, body, status);
}

}  // namespace pipeline
