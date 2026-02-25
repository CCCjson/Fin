#pragma once

#include "pipeline/types.h"
#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>
#include <sqlite3.h>

namespace pipeline {

class SqliteStorage {
public:
    explicit SqliteStorage(const std::string& db_path);
    ~SqliteStorage();

    // 禁止拷贝
    SqliteStorage(const SqliteStorage&) = delete;
    SqliteStorage& operator=(const SqliteStorage&) = delete;

    // 查询每个 symbol 的最新日期 → 用于增量计算
    std::unordered_map<std::string, std::string> load_symbol_latest_dates();

    // 查询所有活跃 A 股的 symbol
    std::vector<std::string> load_active_symbols();

    // 批量 upsert K 线数据，返回实际写入行数
    int upsert_bars(const std::vector<Bar>& bars);

    // 获取数据库路径
    const std::string& db_path() const { return db_path_; }

private:
    void init_pragmas();

    std::string db_path_;
    sqlite3* db_ = nullptr;
    std::mutex write_mtx_;  // 写入需串行（WAL 允许并发读但写仍需串行）
};

}  // namespace pipeline
