#pragma once

#include "pipeline/types.h"
#include "pipeline/blocking_queue.h"
#include "pipeline/sqlite_storage.h"
#include "pipeline/stats_tracker.h"
#include "pipeline/proxy_provider.h"
#include <atomic>
#include <memory>
#include <string>
#include <vector>
#include <unordered_map>
#include <chrono>

namespace pipeline {

class PipelineTask {
public:
    PipelineTask(const FetchRequest& req,
                 SqliteStorage& storage,
                 StatsTracker& stats);

    // 运行任务（阻塞直到完成/停止/失败）
    void run();

    // 停止任务
    void stop() { stop_requested_ = true; }

    // 获取进度
    TaskProgress progress() const;

private:
    // 生产者：从 EastMoney 抓取数据
    void producer_work(const std::vector<std::string>& symbols,
                       const std::unordered_map<std::string, std::string>& latest_dates);

    // 消费者：写入 SQLite
    void consumer_work();

    // 计算增量开始日期
    std::string calc_begin_date(const std::string& symbol,
                                const std::unordered_map<std::string, std::string>& latest_dates) const;

    // 日期 +1 天
    static std::string next_day(const std::string& date_str);

    FetchRequest req_;
    SqliteStorage& storage_;
    StatsTracker& global_stats_;

    // 代理
    std::unique_ptr<ProxyProvider> proxy_provider_;

    // 状态
    std::atomic<TaskState> state_{TaskState::PENDING};
    std::atomic<bool> stop_requested_{false};
    std::atomic<int> total_{0};
    std::atomic<int> done_{0};
    std::atomic<int> failed_{0};
    std::atomic<int> rows_written_{0};
    std::string current_symbol_;
    std::string error_message_;
    std::chrono::steady_clock::time_point start_time_;

    // 生产者-消费者队列
    BlockingQueue<std::vector<Bar>> bar_queue_{20};
};

}  // namespace pipeline
