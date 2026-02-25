#pragma once

#include "pipeline/types.h"
#include "pipeline/pipeline_task.h"
#include "pipeline/sqlite_storage.h"
#include "pipeline/stats_tracker.h"
#include <string>
#include <unordered_map>
#include <memory>
#include <mutex>
#include <thread>

namespace pipeline {

class Orchestrator {
public:
    explicit Orchestrator(const std::string& db_path);
    ~Orchestrator();

    // 提交任务，返回 task_id
    std::string submit_task(FetchRequest req);

    // 查询任务进度
    TaskProgress get_progress(const std::string& task_id) const;

    // 停止任务
    bool stop_task(const std::string& task_id);

    // 全局统计
    nlohmann::json get_stats() const { return stats_.to_json(); }

private:
    std::string generate_task_id() const;

    SqliteStorage storage_;
    StatsTracker stats_;

    struct TaskEntry {
        std::unique_ptr<PipelineTask> task;
        std::thread thread;
    };

    mutable std::mutex tasks_mtx_;
    std::unordered_map<std::string, std::unique_ptr<TaskEntry>> tasks_;
};

}  // namespace pipeline
