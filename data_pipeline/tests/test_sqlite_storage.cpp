#include <gtest/gtest.h>
#include "pipeline/sqlite_storage.h"
#include <filesystem>
#include <sqlite3.h>

using namespace pipeline;

class SqliteStorageTest : public ::testing::Test {
protected:
    std::string test_db_path;

    void SetUp() override {
        test_db_path = "test_pipeline.db";
        // 创建测试数据库和表
        sqlite3* db;
        sqlite3_open(test_db_path.c_str(), &db);

        const char* create_daily = R"(
            CREATE TABLE IF NOT EXISTS daily_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL,
                turnover REAL,
                UNIQUE(symbol, date)
            )
        )";

        const char* create_stock_info = R"(
            CREATE TABLE IF NOT EXISTS stock_info (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        )";

        sqlite3_exec(db, create_daily, nullptr, nullptr, nullptr);
        sqlite3_exec(db, create_stock_info, nullptr, nullptr, nullptr);

        // 插入测试数据
        sqlite3_exec(db, "INSERT INTO stock_info VALUES ('000001.SZ','平安银行','a_share',1)", nullptr, nullptr, nullptr);
        sqlite3_exec(db, "INSERT INTO stock_info VALUES ('600000.SH','浦发银行','a_share',1)", nullptr, nullptr, nullptr);
        sqlite3_exec(db, "INSERT INTO stock_info VALUES ('000002.SZ','万科A','a_share',0)", nullptr, nullptr, nullptr);  // inactive

        sqlite3_close(db);
    }

    void TearDown() override {
        std::filesystem::remove(test_db_path);
        // WAL/SHM files
        std::filesystem::remove(test_db_path + "-wal");
        std::filesystem::remove(test_db_path + "-shm");
    }
};

TEST_F(SqliteStorageTest, LoadActiveSymbols) {
    SqliteStorage storage(test_db_path);
    auto symbols = storage.load_active_symbols();
    EXPECT_EQ(symbols.size(), 2);
}

TEST_F(SqliteStorageTest, UpsertBars) {
    SqliteStorage storage(test_db_path);

    std::vector<Bar> bars = {
        {"000001.SZ", "a_share", "2025-01-02", 10.5, 11.0, 10.3, 10.8, 1000000, 10500000, 1.25},
        {"000001.SZ", "a_share", "2025-01-03", 10.8, 11.2, 10.7, 11.0, 1200000, 13200000, 1.50},
    };

    int written = storage.upsert_bars(bars);
    EXPECT_EQ(written, 2);

    // 验证增量查询
    auto latest = storage.load_symbol_latest_dates();
    EXPECT_EQ(latest.size(), 1);
    EXPECT_EQ(latest["000001.SZ"], "2025-01-03");
}

TEST_F(SqliteStorageTest, UpsertReplace) {
    SqliteStorage storage(test_db_path);

    // 写入
    std::vector<Bar> bars1 = {
        {"000001.SZ", "a_share", "2025-01-02", 10.5, 11.0, 10.3, 10.8, 1000000, 10500000, 1.25},
    };
    storage.upsert_bars(bars1);

    // 覆盖同一天数据
    std::vector<Bar> bars2 = {
        {"000001.SZ", "a_share", "2025-01-02", 10.6, 11.1, 10.4, 10.9, 1100000, 11000000, 1.30},
    };
    int written = storage.upsert_bars(bars2);
    EXPECT_EQ(written, 1);

    // 应该只有一条记录
    auto latest = storage.load_symbol_latest_dates();
    EXPECT_EQ(latest.size(), 1);
}

TEST_F(SqliteStorageTest, UpsertEmpty) {
    SqliteStorage storage(test_db_path);
    int written = storage.upsert_bars({});
    EXPECT_EQ(written, 0);
}
