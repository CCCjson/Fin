#include "pipeline/orchestrator.h"
#include <random>
#include <sstream>
#include <iomanip>
#include <iostream>

namespace pipeline {

Orchestrator::Orchestrator(const std::string& db_path)
    : storage_(db_path) {}

Orchestrator::~Orchestrator() {
    // 停止所有任务并等待线程结束
    std::lock_guard<std::mutex> lock(tasks_mtx_);
    for (auto& [id, entry] : tasks_) {
        entry->task->stop();
        if (entry->thread.joinable()) {
            entry->thread.join();
        }
    }
}

std::string Orchestrator::submit_task(FetchRequest req) {
    std::string task_id = generate_task_id();
    req.task_id = task_id;

    auto entry = std::make_unique<TaskEntry>();
    entry->task = std::make_unique<PipelineTask>(req, storage_, stats_);

    // spawn 线程运行任务
    PipelineTask* task_ptr = entry->task.get();
    entry->thread = std::thread([task_ptr] {
        task_ptr->run();
    });

    std::lock_guard<std::mutex> lock(tasks_mtx_);
    tasks_[task_id] = std::move(entry);

    return task_id;
}

TaskProgress Orchestrator::get_progress(const std::string& task_id) const {
    std::lock_guard<std::mutex> lock(tasks_mtx_);
    auto it = tasks_.find(task_id);
    if (it == tasks_.end()) {
        TaskProgress p;
        p.task_id = task_id;
        p.state = TaskState::FAILED;
        p.error_message = "Task not found";
        return p;
    }
    return it->second->task->progress();
}

bool Orchestrator::stop_task(const std::string& task_id) {
    std::lock_guard<std::mutex> lock(tasks_mtx_);
    auto it = tasks_.find(task_id);
    if (it == tasks_.end()) return false;
    it->second->task->stop();
    return true;
}

std::string Orchestrator::generate_task_id() const {
    // 生成类似 "pipe_a1b2c3d4" 的 ID
    static std::mt19937 rng(std::random_device{}());
    std::uniform_int_distribution<uint32_t> dist;
    std::stringstream ss;
    ss << "pipe_" << std::hex << std::setw(8) << std::setfill('0') << dist(rng);
    return ss.str();
}

}  // namespace pipeline
