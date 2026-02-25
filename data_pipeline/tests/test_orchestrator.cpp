#include <gtest/gtest.h>
#include "pipeline/orchestrator.h"
#include <filesystem>
#include <sqlite3.h>
#include <thread>
#include <chrono>

using namespace pipeline;

class OrchestratorTest : public ::testing::Test {
protected:
    std::string test_db_path = "test_orch.db";

    void SetUp() override {
        sqlite3* db;
        sqlite3_open(test_db_path.c_str(), &db);

        sqlite3_exec(db, R"(
            CREATE TABLE IF NOT EXISTS daily_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL, turnover REAL,
                UNIQUE(symbol, date)
            )
        )", nullptr, nullptr, nullptr);

        sqlite3_exec(db, R"(
            CREATE TABLE IF NOT EXISTS stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        )", nullptr, nullptr, nullptr);

        // 插入少量测试股票
        sqlite3_exec(db, "INSERT INTO stock_info VALUES ('000001.SZ','平安银行','a_share',1)", nullptr, nullptr, nullptr);

        sqlite3_close(db);
    }

    void TearDown() override {
        std::filesystem::remove(test_db_path);
        std::filesystem::remove(test_db_path + "-wal");
        std::filesystem::remove(test_db_path + "-shm");
    }
};

TEST_F(OrchestratorTest, SubmitAndQueryProgress) {
    Orchestrator orch(test_db_path);

    FetchRequest req;
    req.symbols = {"000001.SZ"};
    req.begin_date = "20250101";
    req.end_date = "20250110";
    req.thread_count = 1;

    std::string task_id = orch.submit_task(req);
    EXPECT_FALSE(task_id.empty());

    // 查询进度
    auto prog = orch.get_progress(task_id);
    EXPECT_EQ(prog.task_id, task_id);
    // 状态可能是 PENDING 或 RUNNING
    EXPECT_TRUE(prog.state == TaskState::PENDING || prog.state == TaskState::RUNNING);

    // 等一会让任务完成（实际会发起网络请求，可能失败）
    std::this_thread::sleep_for(std::chrono::seconds(5));

    prog = orch.get_progress(task_id);
    EXPECT_TRUE(prog.state == TaskState::COMPLETED || prog.state == TaskState::FAILED ||
                prog.state == TaskState::RUNNING);
}

TEST_F(OrchestratorTest, StopTask) {
    Orchestrator orch(test_db_path);

    // 提交一个大任务
    FetchRequest req;
    req.begin_date = "20200101";
    req.end_date = "20250101";
    req.thread_count = 2;
    // symbols 为空 → 从 stock_info 加载

    std::string task_id = orch.submit_task(req);

    // 立即停止
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    bool stopped = orch.stop_task(task_id);
    EXPECT_TRUE(stopped);

    // 等停下来
    std::this_thread::sleep_for(std::chrono::seconds(2));
    auto prog = orch.get_progress(task_id);
    // 可能 STOPPED 或 COMPLETED（如果太快完成了）
    EXPECT_TRUE(prog.state == TaskState::STOPPED || prog.state == TaskState::COMPLETED ||
                prog.state == TaskState::RUNNING);
}

TEST_F(OrchestratorTest, GetStats) {
    Orchestrator orch(test_db_path);
    auto stats = orch.get_stats();
    EXPECT_EQ(stats["total_requests"], 0);
    EXPECT_EQ(stats["total_rows_written"], 0);
}

TEST_F(OrchestratorTest, TaskNotFound) {
    Orchestrator orch(test_db_path);
    auto prog = orch.get_progress("nonexistent");
    EXPECT_EQ(prog.state, TaskState::FAILED);
    EXPECT_FALSE(prog.error_message.empty());
}
