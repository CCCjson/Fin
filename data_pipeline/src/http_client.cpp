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

std::string HttpClient::fetch_kline(const std::string& secid,
                                     const std::string& begin_date,
                                     const std::string& end_date) {
    // EastMoney K线 API
    // https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=...&klt=101&fqt=1&...
    std::string path = "/api/qt/stock/kline/get"
                       "?fields1=f1,f2,f3,f4,f5,f6"
                       "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"
                       "&ut=7eea3edcaed734bea9cbfc24409ed989"
                       "&klt=101"
                       "&fqt=1"
                       "&secid=" + secid +
                       "&beg=" + begin_date +
                       "&end=" + end_date;

    for (int attempt = 0; attempt < max_retries_; ++attempt) {
        try {
            httplib::SSLClient cli("push2his.eastmoney.com");
            cli.set_connection_timeout(timeout_sec_);
            cli.set_read_timeout(timeout_sec_);
            cli.set_write_timeout(timeout_sec_);
            cli.enable_server_certificate_verification(false);

            httplib::Headers headers = {
                {"User-Agent", random_ua()},
                {"Accept", "application/json, text/plain, */*"},
                {"Referer", "https://quote.eastmoney.com/"},
                {"Host", "push2his.eastmoney.com"},
            };

            auto res = cli.Get(path, headers);
            if (res && res->status == 200 && !res->body.empty()) {
                return res->body;
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

    return "";  // 所有重试均失败
}

}  // namespace pipeline
