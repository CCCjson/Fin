#include "pipeline/http_client.h"
#include <httplib.h>

#include <thread>
#include <chrono>
#include <iostream>

namespace pipeline {

const std::vector<std::string> HttpClient::USER_AGENTS = {
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
};

HttpClient::HttpClient()
    : rng_(std::random_device{}()) {}

std::string HttpClient::random_ua() {
    std::uniform_int_distribution<size_t> dist(0, USER_AGENTS.size() - 1);
    return USER_AGENTS[dist(rng_)];
}

void HttpClient::set_proxy(const std::string& host, int port,
                            const std::string& user, const std::string& pass) {
    proxy_host_ = host;
    proxy_port_ = port;
    proxy_user_ = user;
    proxy_pass_ = pass;
    use_proxy_ = true;
}

void HttpClient::clear_proxy() {
    proxy_host_.clear();
    proxy_port_ = 0;
    proxy_user_.clear();
    proxy_pass_.clear();
    use_proxy_ = false;
}

std::pair<std::string, bool> HttpClient::fetch_kline(const std::string& secid,
                                                      const std::string& begin_date,
                                                      const std::string& end_date) {
    // EastMoney K线 API
    std::string path = "/api/qt/stock/kline/get"
                       "?fields1=f1,f2,f3,f4,f5,f6"
                       "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"
                       "&ut=7eea3edcaed734bea9cbfc24409ed989"
                       "&klt=101"
                       "&fqt=1"
                       "&secid=" + secid +
                       "&beg=" + begin_date +
                       "&end=" + end_date;

    httplib::Headers headers = {
        {"User-Agent", random_ua()},
        {"Accept", "application/json, text/plain, */*"},
        {"Referer", "https://quote.eastmoney.com/"},
        {"Host", "push2his.eastmoney.com"},
    };

    if (use_proxy_) {
        // ── 代理模式：只请求一次，不重试 ──
        try {
            httplib::Client cli("https://push2his.eastmoney.com");
            cli.set_proxy(proxy_host_.c_str(), proxy_port_);
            if (!proxy_user_.empty()) {
                cli.set_proxy_basic_auth(proxy_user_, proxy_pass_);
            }
            cli.enable_server_certificate_verification(false);
            cli.set_connection_timeout(timeout_sec_);
            cli.set_read_timeout(timeout_sec_);
            cli.set_write_timeout(timeout_sec_);

            auto res = cli.Get(path, headers);

            if (!res)                                       return {"", true};   // 连接失败
            if (res->status == 429)                         return {"", true};   // 限流
            if (res->status == 403)                         return {"", true};   // 封禁
            if (res->status == 200 && res->body.empty())    return {"", true};   // 空响应
            if (res->status == 200)                         return {res->body, false};  // 成功
            return {"", true};  // 其他错误
        } catch (const std::exception& e) {
            std::cerr << "Proxy request error for secid=" << secid << ": " << e.what() << std::endl;
            return {"", true};
        }
    } else {
        // ── 无代理模式：保留原有 3 次重试逻辑 ──
        for (int attempt = 0; attempt < max_retries_; ++attempt) {
            try {
                httplib::SSLClient cli("push2his.eastmoney.com");
                cli.set_connection_timeout(timeout_sec_);
                cli.set_read_timeout(timeout_sec_);
                cli.set_write_timeout(timeout_sec_);
                cli.enable_server_certificate_verification(false);

                auto res = cli.Get(path, headers);
                if (res && res->status == 200 && !res->body.empty()) {
                    return {res->body, false};
                }

                if (res) {
                    std::cerr << "HTTP " << res->status << " for secid=" << secid << std::endl;
                } else {
                    std::cerr << "Connection failed for secid=" << secid << std::endl;
                }
            } catch (const std::exception& e) {
                std::cerr << "Request error for secid=" << secid << ": " << e.what() << std::endl;
            }

            if (attempt < max_retries_ - 1) {
                std::this_thread::sleep_for(std::chrono::milliseconds(retry_delay_ms_ * (attempt + 1)));
            }
        }

        return {"", false};  // 所有重试均失败（无代理不算 banned）
    }
}

}  // namespace pipeline
