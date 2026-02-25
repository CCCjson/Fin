#pragma once

#include <atomic>
#include <chrono>
#include <nlohmann/json.hpp>

namespace pipeline {

// 全局吞吐量统计（原子计数器）
class StatsTracker {
public:
    StatsTracker() : start_time_(std::chrono::steady_clock::now()) {}

    void add_requests(int n = 1) { total_requests_ += n; }
    void add_rows(int n) { total_rows_ += n; }
    void add_errors(int n = 1) { total_errors_ += n; }

    int total_requests() const { return total_requests_.load(); }
    int total_rows() const { return total_rows_.load(); }
    int total_errors() const { return total_errors_.load(); }

    double uptime_seconds() const {
        auto now = std::chrono::steady_clock::now();
        return std::chrono::duration<double>(now - start_time_).count();
    }

    nlohmann::json to_json() const {
        double uptime = uptime_seconds();
        int reqs = total_requests_.load();
        return {
            {"total_requests", reqs},
            {"total_rows_written", total_rows_.load()},
            {"total_errors", total_errors_.load()},
            {"uptime_seconds", uptime},
            {"avg_speed", uptime > 0 ? reqs / uptime : 0}
        };
    }

private:
    std::atomic<int> total_requests_{0};
    std::atomic<int> total_rows_{0};
    std::atomic<int> total_errors_{0};
    std::chrono::steady_clock::time_point start_time_;
};

}  // namespace pipeline
