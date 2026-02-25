/*
 * server.h — REST API 服务器
 *
 * 提供 HTTP 接口，让 Python FastAPI 可以调用 C++ 回测引擎。
 *
 * 端点：
 * GET  /api/strategies       — 获取可用策略列表
 * POST /api/backtest/run     — 运行回测
 * POST /api/backtest/data    — 上传数据（可选）
 *
 * 使用 cpp-httplib 库提供 HTTP 服务。
 */

#pragma once

#include <string>

namespace backtest {

class Server {
public:
    explicit Server(int port = 8002);

    /*
     * start — 启动服务器
     * 这个方法会阻塞（一直运行直到调用 stop()）
     */
    void start();

    /*
     * stop — 停止服务器
     */
    void stop();

private:
    int port_;

    /*
     * 注册所有 API 路由
     * 在 server.cpp 中实现
     */
    void setup_routes();
};

}  // namespace backtest
