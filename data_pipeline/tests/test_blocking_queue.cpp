#include <gtest/gtest.h>
#include "pipeline/blocking_queue.h"
#include "pipeline/thread_pool.h"
#include <thread>
#include <atomic>

using namespace pipeline;

TEST(BlockingQueue, PushPop) {
    BlockingQueue<int> q(5);
    q.push(42);
    int val = 0;
    EXPECT_TRUE(q.pop(val));
    EXPECT_EQ(val, 42);
}

TEST(BlockingQueue, PopTimeout) {
    BlockingQueue<int> q(5);
    int val = 0;
    // 空队列 pop 应该超时返回 false
    EXPECT_FALSE(q.pop(val, 50));
}

TEST(BlockingQueue, Close) {
    BlockingQueue<int> q(5);
    q.push(1);
    q.close();

    int val = 0;
    // close 后仍能取出已有元素
    EXPECT_TRUE(q.pop(val));
    EXPECT_EQ(val, 1);

    // 已空且关闭，返回 false
    EXPECT_FALSE(q.pop(val, 50));
}

TEST(BlockingQueue, ProducerConsumer) {
    BlockingQueue<int> q(3);
    std::atomic<int> sum{0};
    const int N = 100;

    // 消费者线程
    std::thread consumer([&] {
        int val;
        while (q.pop(val, 500)) {
            sum += val;
        }
    });

    // 生产者
    for (int i = 1; i <= N; ++i) {
        q.push(i);
    }
    q.close();

    consumer.join();
    EXPECT_EQ(sum.load(), N * (N + 1) / 2);
}

TEST(ThreadPool, BasicSubmit) {
    ThreadPool pool(2);
    std::atomic<int> counter{0};

    for (int i = 0; i < 10; ++i) {
        pool.submit([&counter] { counter++; });
    }

    pool.wait_all();
    EXPECT_EQ(counter.load(), 10);
}

TEST(ThreadPool, ConcurrentExecution) {
    ThreadPool pool(4);
    std::atomic<int> max_concurrent{0};
    std::atomic<int> current{0};
    std::mutex mtx;

    for (int i = 0; i < 20; ++i) {
        pool.submit([&] {
            int c = ++current;
            {
                // 粗略追踪并发度
                int expected = max_concurrent.load();
                while (c > expected && !max_concurrent.compare_exchange_weak(expected, c)) {}
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            --current;
        });
    }

    pool.wait_all();
    // 至少应该有 >1 的并发度（4 个线程）
    EXPECT_GT(max_concurrent.load(), 1);
}
