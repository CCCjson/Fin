/*
 * main.cpp — 回测服务器入口
 *
 * 启动 HTTP 服务器，监听指定端口（默认 8002）。
 * 支持 Ctrl+C 优雅停机。
 *
 * 用法：
 *   ./backtest_server          # 默认端口 8002
 *   ./backtest_server 9000     # 指定端口 9000
 */

#include "backtest/server.h"
#include <iostream>
#include <csignal>     // signal() — 信号处理

/*
 * 全局指针，用于在信号处理函数中访问 server 对象。
 *
 * 为什么需要全局变量？
 * signal() 的回调函数签名是固定的 void(int)，不能携带额外参数。
 * 所以只能通过全局变量来访问 server 对象。
 *
 * 在生产代码中，通常会用更安全的方式（如 sigaction + 原子变量），
 * 但这里为了简洁用全局指针。
 */
backtest::Server* g_server = nullptr;

/*
 * signal_handler — 处理 Ctrl+C 信号
 *
 * 当用户按 Ctrl+C 时，操作系统会发送 SIGINT 信号。
 * 我们捕获这个信号，优雅地停止服务器。
 */
void signal_handler(int signum) {
    std::cout << "\n收到信号 " << signum << "，正在停止服务器..." << std::endl;
    if (g_server) {
        g_server->stop();
    }
}

int main(int argc, char* argv[]) {
    // 从命令行参数获取端口号
    int port = 8002;
    if (argc > 1) {
        port = std::atoi(argv[1]);   // atoi：字符串转整数
    }

    // 注册信号处理
    signal(SIGINT, signal_handler);    // Ctrl+C
    signal(SIGTERM, signal_handler);   // kill 命令

    // 创建并启动服务器
    backtest::Server server(port);
    g_server = &server;

    server.start();   // 阻塞，直到 stop() 被调用

    std::cout << "服务器已停止" << std::endl;
    return 0;
}
