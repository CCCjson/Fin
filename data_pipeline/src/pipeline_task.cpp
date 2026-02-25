#include "pipeline/pipeline_task.h"
#include "pipeline/http_client.h"
#include "pipeline/kline_parser.h"
#include "pipeline/symbol_mapper.h"
#include "pipeline/thread_pool.h"
#include <thread>
#include <iostream>
#include <sstream>
#include <iomanip>
#include <ctime>

namespace pipeline {

PipelineTask::PipelineTask(const FetchRequest& req,
                           SqliteStorage& storage,
                           StatsTracker& stats)
    : req_(req), storage_(storage), global_stats_(stats) {}

void PipelineTask::run() {
    state_ = TaskState::RUNNING;
    start_time_ = std::chrono::steady_clock::now();

    try {
        // 1. 确定要抓取的 symbols
        std::vector<std::string> symbols = req_.symbols;
        if (symbols.empty()) {
            symbols = storage_.load_active_symbols();
        }
        total_ = static_cast<int>(symbols.size());

        if (total_ == 0) {
            error_message_ = "No symbols to fetch";
            state_ = TaskState::FAILED;
            return;
        }

        // 2. 创建代理提供者（如果有 proxy_api_url）
        if (!req_.proxy_api_url.empty()) {
            proxy_provider_ = std::make_unique<ProxyProvider>(req_.proxy_api_url);
            proxy_provider_->set_switch_every(req_.switch_ip_every);
            std::cout << "[PipelineTask] 代理模式已启用, 每 "
                      << req_.switch_ip_every << " 次请求换 IP" << std::endl;
        }

        // 3. 加载增量日期
        auto latest_dates = storage_.load_symbol_latest_dates();

        // 4. 启动消费者线程
        std::thread consumer_thread([this] { consumer_work(); });

        // 5. 启动生产者线程池
        producer_work(symbols, latest_dates);

        // 6. 关闭队列，等待消费者完成
        bar_queue_.close();
        consumer_thread.join();

        // 7. 设置最终状态
        if (stop_requested_) {
            state_ = TaskState::STOPPED;
        } else {
            state_ = TaskState::COMPLETED;
        }

        if (proxy_provider_) {
            std::cout << "[PipelineTask] 代理切换次数: "
                      << proxy_provider_->switch_count() << std::endl;
        }
    } catch (const std::exception& e) {
        error_message_ = e.what();
        state_ = TaskState::FAILED;
        bar_queue_.close();
    }
}

void PipelineTask::producer_work(const std::vector<std::string>& symbols,
                                  const std::unordered_map<std::string, std::string>& latest_dates) {
    ThreadPool pool(req_.thread_count);

    for (const auto& symbol : symbols) {
        if (stop_requested_) break;

        pool.submit([this, symbol, &latest_dates] {
            if (stop_requested_) return;

            current_symbol_ = symbol;
            std::string code = SymbolMapper::symbol_to_code(symbol);
            std::string secid = SymbolMapper::build_secid(symbol);
            std::string begin = calc_begin_date(symbol, latest_dates);
            std::string end = req_.end_date;

            // 如果 begin > end，说明已经是最新数据，跳过
            if (!begin.empty() && !end.empty() && begin > end) {
                done_++;
                return;
            }

            // 每个线程自己的 HttpClient（SSLClient 非线程安全）
            HttpClient client;

            // 设置代理
            if (proxy_provider_) {
                auto pi = proxy_provider_->get_proxy();
                if (!pi.empty()) {
                    client.set_proxy(pi.host, pi.port, pi.username, pi.password);
                }
            }

            auto [json_str, banned] = client.fetch_kline(secid, begin, end);
            global_stats_.add_requests();
            if (proxy_provider_) {
                proxy_provider_->add_request_count();
            }

            if (banned && proxy_provider_) {
                // IP 被封 → 切换代理，不重试当前 symbol
                proxy_provider_->switch_proxy(client.proxy_host());
                failed_++;
                global_stats_.add_errors();
                return;
            }

            if (json_str.empty()) {
                failed_++;
                global_stats_.add_errors();
                return;
            }

            auto bars = KlineParser::parse(json_str, symbol);
            if (!bars.empty()) {
                bar_queue_.push(std::move(bars));
            }
            done_++;
        });
    }

    pool.wait_all();
}

void PipelineTask::consumer_work() {
    std::vector<Bar> batch;
    while (true) {
        std::vector<Bar> bars;
        if (!bar_queue_.pop(bars, 1000)) {
            // 超时或队列已关闭
            if (bar_queue_.is_closed()) break;
            continue;
        }

        // 累积到 batch
        batch.insert(batch.end(), bars.begin(), bars.end());

        // 达到批次大小则写入
        if (static_cast<int>(batch.size()) >= req_.batch_size) {
            int written = storage_.upsert_bars(batch);
            rows_written_ += written;
            global_stats_.add_rows(written);
            batch.clear();
        }
    }

    // 写入剩余数据
    if (!batch.empty()) {
        int written = storage_.upsert_bars(batch);
        rows_written_ += written;
        global_stats_.add_rows(written);
    }
}

std::string PipelineTask::calc_begin_date(
    const std::string& symbol,
    const std::unordered_map<std::string, std::string>& latest_dates) const {

    auto it = latest_dates.find(symbol);
    if (it != latest_dates.end()) {
        // 已有数据，从最新日期 +1 天开始
        std::string nd = next_day(it->second);
        // 转为 YYYYMMDD 格式（去掉 -）
        std::string result;
        for (char c : nd) {
            if (c != '-') result += c;
        }
        return result;
    }
    // 没有数据，用请求的 begin_date
    return req_.begin_date;
}

std::string PipelineTask::next_day(const std::string& date_str) {
    // 支持 "YYYY-MM-DD" 格式
    std::tm tm = {};
    std::istringstream ss(date_str);
    ss >> std::get_time(&tm, "%Y-%m-%d");
    if (ss.fail()) {
        // 尝试 "YYYYMMDD" 格式
        ss.clear();
        ss.str(date_str);
        ss >> std::get_time(&tm, "%Y%m%d");
        if (ss.fail()) return date_str;
    }

    tm.tm_mday += 1;
    std::mktime(&tm);  // 规范化（处理月末进位等）

    char buf[16];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d", &tm);
    return buf;
}

TaskProgress PipelineTask::progress() const {
    TaskProgress p;
    p.task_id = req_.task_id;
    p.state = state_.load();
    p.total = total_.load();
    p.done = done_.load();
    p.failed = failed_.load();
    p.rows_written = rows_written_.load();
    p.current_symbol = current_symbol_;
    p.error_message = error_message_;

    if (proxy_provider_) {
        p.proxy_switches = proxy_provider_->switch_count();
    }

    auto now = std::chrono::steady_clock::now();
    p.elapsed_seconds = std::chrono::duration<double>(now - start_time_).count();

    return p;
}

}  // namespace pipeline
