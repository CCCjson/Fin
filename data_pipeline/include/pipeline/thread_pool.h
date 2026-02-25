#pragma once

#include <vector>
#include <thread>
#include <functional>
#include <future>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <atomic>

namespace pipeline {

class ThreadPool {
public:
    explicit ThreadPool(size_t num_threads) : stop_(false) {
        for (size_t i = 0; i < num_threads; ++i) {
            workers_.emplace_back([this] {
                while (true) {
                    std::function<void()> task;
                    {
                        std::unique_lock<std::mutex> lock(mtx_);
                        cv_.wait(lock, [this] { return stop_ || !tasks_.empty(); });
                        if (stop_ && tasks_.empty()) return;
                        task = std::move(tasks_.front());
                        tasks_.pop();
                    }
                    task();
                    ++completed_;
                }
            });
        }
    }

    ~ThreadPool() {
        {
            std::lock_guard<std::mutex> lock(mtx_);
            stop_ = true;
        }
        cv_.notify_all();
        for (auto& w : workers_) {
            if (w.joinable()) w.join();
        }
    }

    // 提交任务
    template<typename F>
    void submit(F&& f) {
        {
            std::lock_guard<std::mutex> lock(mtx_);
            ++submitted_;
            tasks_.push(std::forward<F>(f));
        }
        cv_.notify_one();
    }

    // 等待所有已提交任务完成
    void wait_all() {
        while (completed_.load() < submitted_.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    }

    size_t submitted_count() const { return submitted_.load(); }
    size_t completed_count() const { return completed_.load(); }

private:
    std::vector<std::thread> workers_;
    std::queue<std::function<void()>> tasks_;
    std::mutex mtx_;
    std::condition_variable cv_;
    bool stop_;
    std::atomic<size_t> submitted_{0};
    std::atomic<size_t> completed_{0};
};

}  // namespace pipeline
