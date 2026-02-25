#pragma once

#include <string>
#include <vector>
#include <random>
#include <utility>

namespace pipeline {

// 封装 cpp-httplib SSLClient（EastMoney HTTPS）
// 注意: 每个生产者线程应创建自己的 HttpClient 实例（SSLClient 非线程安全）
class HttpClient {
public:
    HttpClient();

    // 设置代理
    void set_proxy(const std::string& host, int port,
                   const std::string& user = "", const std::string& pass = "");
    void clear_proxy();

    // 获取股票 K 线数据 JSON
    // secid: "0.000001" 或 "1.600000"
    // begin_date/end_date: "20250101" 格式
    // 返回 <json_body, is_proxy_banned>
    //   is_proxy_banned=true 表示疑似 IP 被封，调用方应切换代理
    //   无代理模式下 is_proxy_banned 始终为 false
    std::pair<std::string, bool> fetch_kline(const std::string& secid,
                                              const std::string& begin_date,
                                              const std::string& end_date);

    // 获取当前代理 host（用于 switch_proxy 时传入）
    const std::string& proxy_host() const { return proxy_host_; }

private:
    std::string random_ua();

    static const std::vector<std::string> USER_AGENTS;
    std::mt19937 rng_;
    int max_retries_ = 3;
    int retry_delay_ms_ = 500;
    int timeout_sec_ = 10;

    // 代理
    std::string proxy_host_;
    int proxy_port_ = 0;
    std::string proxy_user_;
    std::string proxy_pass_;
    bool use_proxy_ = false;
};

}  // namespace pipeline
