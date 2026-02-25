#pragma once

#include <queue>
#include <mutex>
#include <condition_variable>
#include <chrono>

namespace pipeline {

// 线程安全阻塞队列（header-only 模板）
template<typename T>
class BlockingQueue {
public:
    explicit BlockingQueue(size_t capacity = 20)
        : capacity_(capacity), closed_(false) {}

    // push — 满了就阻塞等待
    void push(T item) {
        std::unique_lock<std::mutex> lock(mtx_);
        not_full_.wait(lock, [this] { return queue_.size() < capacity_ || closed_; });
        if (closed_) return;
        queue_.push(std::move(item));
        not_empty_.notify_one();
    }

    // pop — 空了就阻塞等待，超时返回 false
    bool pop(T& item, int timeout_ms = 1000) {
        std::unique_lock<std::mutex> lock(mtx_);
        bool got = not_empty_.wait_for(lock, std::chrono::milliseconds(timeout_ms),
            [this] { return !queue_.empty() || closed_; });
        if (!got || (queue_.empty() && closed_)) return false;
        if (queue_.empty()) return false;
        item = std::move(queue_.front());
        queue_.pop();
        not_full_.notify_one();
        return true;
    }

    // 关闭队列，通知所有消费者不再有新数据
    void close() {
        std::lock_guard<std::mutex> lock(mtx_);
        closed_ = true;
        not_empty_.notify_all();
        not_full_.notify_all();
    }

    bool is_closed() const {
        std::lock_guard<std::mutex> lock(mtx_);
        return closed_ && queue_.empty();
    }

    size_t size() const {
        std::lock_guard<std::mutex> lock(mtx_);
        return queue_.size();
    }

private:
    std::queue<T> queue_;
    size_t capacity_;
    bool closed_;
    mutable std::mutex mtx_;
    std::condition_variable not_empty_;
    std::condition_variable not_full_;
};

}  // namespace pipeline
