#include "pipeline/proxy_provider.h"
#include <httplib.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <sstream>

namespace pipeline {

ProxyProvider::ProxyProvider(const std::string& api_url)
    : api_url_(api_url) {}

void ProxyProvider::set_switch_every(int n) {
    switch_every_ = n > 0 ? n : 800;
}

int ProxyProvider::switch_count() const {
    return switch_count_.load();
}

void ProxyProvider::add_request_count(int n) {
    request_count_ += n;
}

ProxyProvider::ProxyInfo ProxyProvider::get_proxy() {
    std::lock_guard<std::mutex> lock(mtx_);

    // 有代理且未到主动切换阈值 → 返回当前
    if (!current_.empty() && request_count_.load() < switch_every_) {
        return current_;
    }

    // 需要刷新
    auto pi = fetch_from_api();
    if (!pi.empty()) {
        current_ = pi;
        request_count_ = 0;
        switch_count_++;
        std::cout << "[ProxyProvider] 获取代理: " << pi.host << ":" << pi.port << std::endl;
    }
    return current_;
}

ProxyProvider::ProxyInfo ProxyProvider::switch_proxy(const std::string& failed_host) {
    std::lock_guard<std::mutex> lock(mtx_);

    // 检查当前代理是否就是失败的那个（避免多线程重复切换）
    if (current_.host != failed_host) {
        // 已经被其他线程切换了，直接返回新的
        return current_;
    }

    auto pi = fetch_from_api();
    if (!pi.empty()) {
        current_ = pi;
        request_count_ = 0;
        switch_count_++;
        std::cout << "[ProxyProvider] IP被封，切换代理: " << pi.host << ":" << pi.port << std::endl;
    } else {
        std::cerr << "[ProxyProvider] 切换代理失败，API 无响应" << std::endl;
    }
    return current_;
}

ProxyProvider::ProxyInfo ProxyProvider::fetch_from_api() {
    // 从 api_url_ 解析 host 和 path
    // URL 格式: https://dps.kdlapi.com/api/getdps/?xxx 或 http://...
    try {
        std::string url = api_url_;
        std::string scheme, host, path;
        int port = 443;

        // 解析 scheme
        if (url.substr(0, 8) == "https://") {
            scheme = "https";
            url = url.substr(8);
        } else if (url.substr(0, 7) == "http://") {
            scheme = "http";
            port = 80;
            url = url.substr(7);
        } else {
            std::cerr << "[ProxyProvider] 无效的 API URL: " << api_url_ << std::endl;
            return {};
        }

        // 分离 host 和 path
        auto slash_pos = url.find('/');
        if (slash_pos != std::string::npos) {
            host = url.substr(0, slash_pos);
            path = url.substr(slash_pos);
        } else {
            host = url;
            path = "/";
        }

        // 检查 host 中是否有端口号
        auto colon_pos = host.find(':');
        if (colon_pos != std::string::npos) {
            port = std::stoi(host.substr(colon_pos + 1));
            host = host.substr(0, colon_pos);
        }

        // 发起请求（直连，不走代理）
        std::unique_ptr<httplib::Client> cli;
        if (scheme == "https") {
            std::string base = "https://" + host + ":" + std::to_string(port);
            cli = std::make_unique<httplib::Client>(base);
            cli->enable_server_certificate_verification(false);
        } else {
            std::string base = "http://" + host + ":" + std::to_string(port);
            cli = std::make_unique<httplib::Client>(base);
        }

        cli->set_connection_timeout(10);
        cli->set_read_timeout(10);

        auto res = cli->Get(path);
        if (!res) {
            std::cerr << "[ProxyProvider] API 请求失败: 连接错误" << std::endl;
            return {};
        }
        if (res->status != 200) {
            std::cerr << "[ProxyProvider] API 返回 HTTP " << res->status << std::endl;
            return {};
        }

        return parse_response(res->body);

    } catch (const std::exception& e) {
        std::cerr << "[ProxyProvider] API 请求异常: " << e.what() << std::endl;
        return {};
    }
}

ProxyProvider::ProxyInfo ProxyProvider::parse_response(const std::string& body) {
    // 快代理 API 返回格式（JSON 模式）:
    // {"code": 0, "data": {"proxy_list": ["ip:port"], ...}, "msg": "成功"}
    // 或者纯文本模式: "ip:port\r\n"
    // 也可能是带认证的: "ip:port:user:pass"

    ProxyInfo pi;

    // 先尝试 JSON 解析
    try {
        auto j = nlohmann::json::parse(body);

        if (j.contains("code") && j["code"].get<int>() == 0) {
            // 标准 JSON 响应
            if (j.contains("data") && j["data"].contains("proxy_list")) {
                auto& list = j["data"]["proxy_list"];
                if (!list.empty()) {
                    std::string proxy_str = list[0].get<std::string>();
                    // 解析 "ip:port" 或 "ip:port:user:pass"
                    std::istringstream ss(proxy_str);
                    std::string token;
                    std::vector<std::string> parts;
                    while (std::getline(ss, token, ':')) {
                        parts.push_back(token);
                    }
                    if (parts.size() >= 2) {
                        pi.host = parts[0];
                        pi.port = std::stoi(parts[1]);
                    }
                    if (parts.size() >= 4) {
                        pi.username = parts[2];
                        pi.password = parts[3];
                    }
                }
            }
        } else {
            std::cerr << "[ProxyProvider] API 返回错误: "
                      << j.value("msg", "unknown") << std::endl;
        }
        return pi;
    } catch (const nlohmann::json::exception&) {
        // 非 JSON，尝试纯文本解析
    }

    // 纯文本模式: "ip:port\r\n" 或 "ip:port:user:pass\r\n"
    std::string line = body;
    // 去掉换行符
    while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) {
        line.pop_back();
    }

    if (!line.empty()) {
        std::istringstream ss(line);
        std::string token;
        std::vector<std::string> parts;
        while (std::getline(ss, token, ':')) {
            parts.push_back(token);
        }
        if (parts.size() >= 2) {
            pi.host = parts[0];
            pi.port = std::stoi(parts[1]);
        }
        if (parts.size() >= 4) {
            pi.username = parts[2];
            pi.password = parts[3];
        }
    }

    return pi;
}

}  // namespace pipeline
