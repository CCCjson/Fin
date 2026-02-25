#pragma once

#include "pipeline/orchestrator.h"
#include <httplib.h>
#include <nlohmann/json.hpp>
#include <string>

namespace pipeline {

class Server {
public:
    Server(int port, const std::string& db_path);

    void start();
    void stop();

private:
    void setup_routes();

    // 路由处理
    void handle_fetch(const httplib::Request& req, httplib::Response& res);
    void handle_status(const httplib::Request& req, httplib::Response& res);
    void handle_stop(const httplib::Request& req, httplib::Response& res);
    void handle_stats(const httplib::Request& req, httplib::Response& res);

    // 工具函数
    void json_response(httplib::Response& res, const nlohmann::json& body, int status = 200);
    void error_response(httplib::Response& res, const std::string& message, int status = 400);

    int port_;
    httplib::Server svr_;
    Orchestrator orchestrator_;
};

}  // namespace pipeline
