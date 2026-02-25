#include "pipeline/sqlite_storage.h"
#include <stdexcept>
#include <iostream>

namespace pipeline {

SqliteStorage::SqliteStorage(const std::string& db_path) : db_path_(db_path) {
    int rc = sqlite3_open(db_path.c_str(), &db_);
    if (rc != SQLITE_OK) {
        std::string err = sqlite3_errmsg(db_);
        sqlite3_close(db_);
        throw std::runtime_error("Failed to open database: " + err);
    }
    init_pragmas();
}

SqliteStorage::~SqliteStorage() {
    if (db_) {
        sqlite3_close(db_);
        db_ = nullptr;
    }
}

void SqliteStorage::init_pragmas() {
    // WAL 模式允许 C++ 和 Python 并发读写
    char* err = nullptr;
    sqlite3_exec(db_, "PRAGMA journal_mode=WAL;", nullptr, nullptr, &err);
    if (err) { sqlite3_free(err); }

    sqlite3_exec(db_, "PRAGMA synchronous=NORMAL;", nullptr, nullptr, &err);
    if (err) { sqlite3_free(err); }

    // 增加 busy timeout，避免 SQLITE_BUSY
    sqlite3_busy_timeout(db_, 5000);
}

std::unordered_map<std::string, std::string> SqliteStorage::load_symbol_latest_dates() {
    std::unordered_map<std::string, std::string> result;
    const char* sql = "SELECT symbol, MAX(date) FROM daily_quotes WHERE market='a_share' GROUP BY symbol";

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
    if (rc != SQLITE_OK) {
        std::cerr << "Failed to prepare latest dates query: " << sqlite3_errmsg(db_) << std::endl;
        return result;
    }

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        const char* sym = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        const char* dt = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
        if (sym && dt) {
            result[sym] = dt;
        }
    }
    sqlite3_finalize(stmt);
    return result;
}

std::vector<std::string> SqliteStorage::load_active_symbols() {
    std::vector<std::string> result;
    const char* sql = "SELECT symbol FROM stock_info WHERE market='a_share' AND is_active=1";

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
    if (rc != SQLITE_OK) {
        std::cerr << "Failed to prepare active symbols query: " << sqlite3_errmsg(db_) << std::endl;
        return result;
    }

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        const char* sym = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
        if (sym) {
            result.emplace_back(sym);
        }
    }
    sqlite3_finalize(stmt);
    return result;
}

int SqliteStorage::upsert_bars(const std::vector<Bar>& bars) {
    if (bars.empty()) return 0;

    std::lock_guard<std::mutex> lock(write_mtx_);

    const char* sql =
        "INSERT OR REPLACE INTO daily_quotes "
        "(symbol, market, date, open, high, low, close, volume, amount, turnover) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";

    sqlite3_stmt* stmt = nullptr;
    int rc = sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr);
    if (rc != SQLITE_OK) {
        std::cerr << "Failed to prepare upsert: " << sqlite3_errmsg(db_) << std::endl;
        return 0;
    }

    char* err = nullptr;
    sqlite3_exec(db_, "BEGIN TRANSACTION", nullptr, nullptr, &err);
    if (err) { sqlite3_free(err); }

    int written = 0;
    for (const auto& bar : bars) {
        sqlite3_bind_text(stmt, 1, bar.symbol.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 2, bar.market.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 3, bar.date.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_double(stmt, 4, bar.open);
        sqlite3_bind_double(stmt, 5, bar.high);
        sqlite3_bind_double(stmt, 6, bar.low);
        sqlite3_bind_double(stmt, 7, bar.close);
        sqlite3_bind_double(stmt, 8, bar.volume);
        sqlite3_bind_double(stmt, 9, bar.amount);
        sqlite3_bind_double(stmt, 10, bar.turnover);

        rc = sqlite3_step(stmt);
        if (rc == SQLITE_DONE) {
            ++written;
        } else {
            std::cerr << "Upsert failed for " << bar.symbol << " " << bar.date
                      << ": " << sqlite3_errmsg(db_) << std::endl;
        }
        sqlite3_reset(stmt);
    }

    sqlite3_exec(db_, "COMMIT", nullptr, nullptr, &err);
    if (err) { sqlite3_free(err); }

    sqlite3_finalize(stmt);
    return written;
}

}  // namespace pipeline
