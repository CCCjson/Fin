/*
 * ============================================================
 * server.h — REST API 服务器
 * ============================================================
 *
 * 使用 cpp-httplib 库搭建一个 HTTP 服务器，
 * 对外提供 REST API 接口，让前端/Python 后端可以通过 HTTP 请求
 * 来操作订单簿。
 *
 * cpp-httplib 是一个"头文件 only"的 C++ HTTP 库：
 *   - 不需要额外编译，只需要 #include 就能用
 *   - 支持 GET / POST / DELETE 等所有 HTTP 方法
 *   - 用 lambda 函数注册路由处理器
 *
 * ============================================================
 */

#ifndef ORDERBOOK_SERVER_H
#define ORDERBOOK_SERVER_H

#include <string>
#include <httplib.h>   // cpp-httplib
#include "orderbook/session_manager.h"

namespace orderbook {

class Server {
public:
    /// 创建服务器
    /// port — 监听端口（默认 8001）
    explicit Server(int port = 8001);

    /// 启动服务器（阻塞调用，会一直运行直到停止）
    void start();

    /// 停止服务器
    void stop();

private:
    int port_;
    httplib::Server svr_;          // cpp-httplib 的服务器对象
    SessionManager manager_;       // 会话管理器

    /// 注册所有 API 路由
    void setup_routes();

    // ── 各个路由的处理函数 ──
    // 参数：
    //   req — HTTP 请求（包含 URL 路径、请求体、查询参数等）
    //   res — HTTP 响应（我们往里面写返回内容）

    /// POST /api/sessions — 创建会话
    void handle_create_session(const httplib::Request& req, httplib::Response& res);

    /// GET /api/sessions/:id/depth — 获取盘口深度
    void handle_get_depth(const httplib::Request& req, httplib::Response& res);

    /// POST /api/sessions/:id/orders — 提交订单
    void handle_submit_order(const httplib::Request& req, httplib::Response& res);

    /// DELETE /api/sessions/:id/orders/:oid — 撤单
    void handle_cancel_order(const httplib::Request& req, httplib::Response& res);

    /// GET /api/sessions/:id/fills — 获取成交记录
    void handle_get_fills(const httplib::Request& req, httplib::Response& res);

    /// GET /api/sessions/:id/stats — 获取统计
    void handle_get_stats(const httplib::Request& req, httplib::Response& res);

    /// POST /api/sessions/:id/seed — 随机播种
    void handle_seed_orders(const httplib::Request& req, httplib::Response& res);

    // ── 工具 ──

    /// 设置 JSON 响应
    void json_response(httplib::Response& res, const nlohmann::json& body, int status = 200);

    /// 设置错误响应
    void error_response(httplib::Response& res, const std::string& message, int status = 400);

    /// 从路径中提取参数（如 /api/sessions/:id 中的 :id）
    /// cpp-httplib 用 req.matches[N] 来获取路径参数
};

}  // namespace orderbook

#endif // ORDERBOOK_SERVER_H
