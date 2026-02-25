#pragma once

#include <string>
#include <vector>
#include <nlohmann/json.hpp>

namespace pipeline {

// ── K线数据 ──
struct Bar {
    std::string symbol;     // "000001.SZ"
    std::string market;     // "a_share"
    std::string date;       // "2025-01-01"
    double open = 0;
    double high = 0;
    double low = 0;
    double close = 0;
    double volume = 0;
    double amount = 0;
    double turnover = 0;    // 换手率

    nlohmann::json to_json() const {
        return {
            {"symbol", symbol}, {"market", market}, {"date", date},
            {"open", open}, {"high", high}, {"low", low}, {"close", close},
            {"volume", volume}, {"amount", amount}, {"turnover", turnover}
        };
    }
};

// ── 任务状态 ──
enum class TaskState {
    PENDING,
    RUNNING,
    COMPLETED,
    FAILED,
    STOPPED
};

inline std::string task_state_to_string(TaskState s) {
    switch (s) {
        case TaskState::PENDING:   return "PENDING";
        case TaskState::RUNNING:   return "RUNNING";
        case TaskState::COMPLETED: return "COMPLETED";
        case TaskState::FAILED:    return "FAILED";
        case TaskState::STOPPED:   return "STOPPED";
        default:                   return "UNKNOWN";
    }
}

// ── 任务进度 ──
struct TaskProgress {
    std::string task_id;
    TaskState state = TaskState::PENDING;
    int total = 0;
    int done = 0;
    int failed = 0;
    int rows_written = 0;
    double elapsed_seconds = 0;
    std::string current_symbol;
    std::string error_message;

    int proxy_switches = 0;     // 代理切换次数

    nlohmann::json to_json() const {
        auto j = nlohmann::json{
            {"task_id", task_id},
            {"state", task_state_to_string(state)},
            {"total", total},
            {"done", done},
            {"failed", failed},
            {"rows_written", rows_written},
            {"elapsed_seconds", elapsed_seconds},
            {"current_symbol", current_symbol},
            {"error_message", error_message},
            {"speed", elapsed_seconds > 0 ? done / elapsed_seconds : 0}
        };
        if (proxy_switches > 0) {
            j["proxy_switches"] = proxy_switches;
        }
        return j;
    }
};

// ── 抓取请求 ──
struct FetchRequest {
    std::string task_id;
    std::vector<std::string> symbols;   // 空 = 全部活跃 A 股
    std::string begin_date;             // "20250101"
    std::string end_date;               // "20250201"
    int thread_count = 4;
    int batch_size = 500;
    std::string proxy_api_url;          // 快代理 API URL，空=不使用代理
    int switch_ip_every = 800;          // 每 N 次请求主动换 IP

    static FetchRequest from_json(const nlohmann::json& j) {
        FetchRequest req;
        if (j.contains("task_id"))        req.task_id = j["task_id"].get<std::string>();
        if (j.contains("symbols"))        req.symbols = j["symbols"].get<std::vector<std::string>>();
        if (j.contains("begin_date"))     req.begin_date = j["begin_date"].get<std::string>();
        if (j.contains("end_date"))       req.end_date = j["end_date"].get<std::string>();
        if (j.contains("thread_count"))   req.thread_count = j["thread_count"].get<int>();
        if (j.contains("batch_size"))     req.batch_size = j["batch_size"].get<int>();
        if (j.contains("proxy_api_url"))  req.proxy_api_url = j["proxy_api_url"].get<std::string>();
        if (j.contains("switch_ip_every")) req.switch_ip_every = j["switch_ip_every"].get<int>();
        return req;
    }
};

}  // namespace pipeline
