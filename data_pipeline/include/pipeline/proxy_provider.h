#pragma once

#include <string>
#include <mutex>
#include <atomic>

namespace pipeline {

// 线程安全的代理 IP 管理器
// 从快代理 (Kuaidaili) API 获取代理 IP，支持自动刷新和被封切换
class ProxyProvider {
public:
    struct ProxyInfo {
        std::string host;
        int port = 0;
        std::string username;
        std::string password;
        bool empty() const { return host.empty(); }
    };

    explicit ProxyProvider(const std::string& api_url);

    // 获取当前可用代理（过期或达到切换阈值则自动刷新）
    ProxyInfo get_proxy();

    // 当前代理被封，立即切换（传入旧 host 避免多线程重复切换）
    ProxyInfo switch_proxy(const std::string& failed_host);

    // 累加请求计数（用于主动换 IP）
    void add_request_count(int n = 1);

    // 设置每 N 次请求主动换 IP
    void set_switch_every(int n);

    // 统计：总共换了多少次 IP
    int switch_count() const;

private:
    ProxyInfo fetch_from_api();
    ProxyInfo parse_response(const std::string& json_str);

    std::string api_url_;
    ProxyInfo current_;
    std::mutex mtx_;
    std::atomic<int> request_count_{0};
    std::atomic<int> switch_count_{0};
    int switch_every_ = 800;
};

}  // namespace pipeline
