#pragma once

#include <string>
#include <vector>
#include <random>

namespace pipeline {

// 封装 cpp-httplib SSLClient（EastMoney HTTPS）
// 注意: 每个生产者线程应创建自己的 HttpClient 实例（SSLClient 非线程安全）
class HttpClient {
public:
    HttpClient();

    // 获取股票 K 线数据 JSON
    // secid: "0.000001" 或 "1.600000"
    // begin_date/end_date: "20250101" 格式
    // 返回 JSON 字符串，失败返回空
    std::string fetch_kline(const std::string& secid,
                            const std::string& begin_date,
                            const std::string& end_date);

private:
    std::string random_ua();

    static const std::vector<std::string> USER_AGENTS;
    std::mt19937 rng_;
    int max_retries_ = 3;
    int retry_delay_ms_ = 500;
    int timeout_sec_ = 10;
};

}  // namespace pipeline
