/*
 * main.cpp — C++ 数据管道服务入口
 *
 * 运行方式：
 *   ./pipeline_server                              ← 默认端口 8003，默认 db 路径
 *   ./pipeline_server 8003 ../../backend/data/market.db
 */

#include <iostream>
#include <csignal>
#include "pipeline/server.h"

pipeline::Server* g_server = nullptr;

void signal_handler(int signal) {
    if (signal == SIGINT || signal == SIGTERM) {
        std::cout << "\n收到停止信号，正在关闭数据管道服务..." << std::endl;
        if (g_server) {
            g_server->stop();
        }
    }
}

int main(int argc, char* argv[]) {
    int port = 8003;
    std::string db_path = "../../backend/data/market.db";

    if (argc > 1) {
        try {
            port = std::stoi(argv[1]);
        } catch (...) {
            std::cerr << "无效的端口号: " << argv[1] << std::endl;
            std::cerr << "用法: " << argv[0] << " [port] [db_path]" << std::endl;
            return 1;
        }
    }

    if (argc > 2) {
        db_path = argv[2];
    }

    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    pipeline::Server server(port, db_path);
    g_server = &server;

    server.start();

    std::cout << "数据管道服务已关闭" << std::endl;
    return 0;
}
