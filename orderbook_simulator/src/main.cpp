/*
 * ============================================================
 * main.cpp — 程序入口
 * ============================================================
 *
 * C++ 程序的入口是 main 函数（就像 Python 的 if __name__ == "__main__"）。
 * 这里做的事情很简单：
 *   1. 解析命令行参数（端口号）
 *   2. 创建服务器对象
 *   3. 启动服务器
 *
 * 运行方式：
 *   ./orderbook_server            ← 默认端口 8001
 *   ./orderbook_server 9001       ← 指定端口 9001
 *
 * ============================================================
 */

#include <iostream>     // std::cout, std::cerr
#include <csignal>      // signal — 处理 Ctrl+C 信号
#include "orderbook/server.h"

// ── 全局指针，用于信号处理 ──
// 这里用裸指针而不是智能指针，是因为信号处理函数必须是全局的 C 风格函数
// 信号处理函数不能访问局部变量，所以需要一个全局指针指向服务器
orderbook::Server* g_server = nullptr;

// ── 信号处理函数 ──
// 当用户按 Ctrl+C 时，操作系统会发送 SIGINT 信号给进程
// 我们捕获这个信号，优雅地停止服务器（而不是直接崩溃）
void signal_handler(int signal) {
    if (signal == SIGINT || signal == SIGTERM) {
        std::cout << "\n收到停止信号，正在关闭服务器..." << std::endl;
        if (g_server) {
            g_server->stop();
        }
    }
}


int main(int argc, char* argv[]) {
    // argc = argument count（参数数量）
    // argv = argument vector（参数数组）
    //   argv[0] = 程序自身的路径（如 "./orderbook_server"）
    //   argv[1] = 第一个参数（如 "9001"）

    // 解析端口号
    int port = 8001;   // 默认端口
    if (argc > 1) {
        try {
            port = std::stoi(argv[1]);
            // std::stoi = string to integer
        } catch (...) {
            std::cerr << "无效的端口号: " << argv[1] << std::endl;
            std::cerr << "用法: " << argv[0] << " [port]" << std::endl;
            return 1;   // 返回非 0 表示出错
        }
    }

    // 注册信号处理
    std::signal(SIGINT, signal_handler);    // Ctrl+C
    std::signal(SIGTERM, signal_handler);   // kill 命令

    // 创建并启动服务器
    orderbook::Server server(port);
    g_server = &server;   // 让全局指针指向这个服务器

    server.start();   // 阻塞调用，直到 stop() 被调用

    std::cout << "服务器已关闭" << std::endl;
    return 0;   // 返回 0 表示正常退出
}
