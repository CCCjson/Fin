/*
 * data_loader.cpp — 数据加载器的实现
 */

#include "backtest/data_loader.h"
#include <fstream>     // 文件读写
#include <sstream>     // 字符串流（用于解析 CSV）
#include <random>      // 随机数生成
#include <stdexcept>   // 异常类

namespace backtest {

/*
 * from_json — 从 JSON 数组加载 K 线数据
 *
 * nlohmann/json 的使用方法：
 * - j.is_array()：检查是否为数组
 * - j["key"].get<T>()：获取某个字段的值并转为类型 T
 * - j.value("key", default)：获取字段，如果不存在用默认值
 *
 * 为什么用 value() 而不是 get()？
 * get() 在字段不存在时会抛异常，value() 会返回默认值。
 * 对于 volume 等可选字段，value() 更安全。
 */
std::vector<Bar> DataLoader::from_json(const nlohmann::json& j) {
    std::vector<Bar> bars;

    if (!j.is_array()) {
        throw std::runtime_error("JSON data must be an array");
    }

    /*
     * reserve()：预先分配内存
     * vector 在添加元素时可能需要重新分配内存（类似搬家），
     * 如果我们提前知道大约要放多少元素，reserve() 一步到位，
     * 避免反复搬家，提升性能。
     */
    bars.reserve(j.size());

    for (const auto& item : j) {
        Bar bar;
        bar.date   = item.value("date", "");
        bar.open   = item.value("open", 0.0);
        bar.high   = item.value("high", 0.0);
        bar.low    = item.value("low", 0.0);
        bar.close  = item.value("close", 0.0);
        bar.volume = item.value("volume", 0.0);
        bars.push_back(bar);
    }

    return bars;
}

/*
 * from_csv — 从 CSV 文件加载数据
 *
 * CSV (Comma-Separated Values) 是最简单的数据格式，
 * 每行一条记录，字段之间用逗号分隔。
 *
 * 解析方法：
 * 1. 用 ifstream 打开文件
 * 2. getline() 逐行读取
 * 3. stringstream + getline(ss, field, ',') 按逗号分割
 * 4. stod() 把字符串转为 double
 *
 * std::ifstream：输入文件流（input file stream）
 * std::getline()：从流中读取一整行
 * std::stringstream：把字符串当作流来操作（方便分割）
 */
std::vector<Bar> DataLoader::from_csv(const std::string& filepath) {
    std::vector<Bar> bars;

    std::ifstream file(filepath);
    if (!file.is_open()) {
        throw std::runtime_error("Cannot open file: " + filepath);
    }

    std::string line;
    bool first_line = true;   // 跳过表头

    while (std::getline(file, line)) {
        if (first_line) {
            first_line = false;
            continue;   // 跳过第一行（表头）
        }

        if (line.empty()) continue;   // 跳过空行

        /*
         * 解析一行 CSV
         * 例如："2025-01-15,100.5,105.2,99.8,103.0,1500000"
         *
         * stringstream 把这个字符串变成一个"流"，
         * 然后用 getline(ss, field, ',') 以逗号为分隔符读取每个字段。
         */
        std::stringstream ss(line);
        std::string field;
        std::vector<std::string> fields;

        while (std::getline(ss, field, ',')) {
            fields.push_back(field);
        }

        // 至少需要 6 个字段：date, open, high, low, close, volume
        if (fields.size() < 6) continue;

        Bar bar;
        bar.date   = fields[0];
        /*
         * std::stod()：string to double（字符串转浮点数）
         * 如果字符串格式不对（如 "abc"），会抛出 std::invalid_argument 异常。
         *
         * try-catch：异常处理机制
         * try 块里的代码如果抛出异常，会被 catch 块捕获。
         * 这里如果某一行数据格式有问题，我们跳过它，继续处理下一行。
         */
        try {
            bar.open   = std::stod(fields[1]);
            bar.high   = std::stod(fields[2]);
            bar.low    = std::stod(fields[3]);
            bar.close  = std::stod(fields[4]);
            bar.volume = std::stod(fields[5]);
        } catch (...) {
            continue;   // 解析失败，跳过这行
        }

        bars.push_back(bar);
    }

    return bars;
}

/*
 * to_json — 把 Bar 数组序列化为 JSON
 *
 * nlohmann/json 的构造方式：
 * - nlohmann::json::array()：创建空数组
 * - json obj = { {"key", value}, ... }：创建对象
 * - array.push_back(obj)：往数组里添加元素
 */
nlohmann::json DataLoader::to_json(const std::vector<Bar>& bars) {
    nlohmann::json arr = nlohmann::json::array();
    for (const auto& bar : bars) {
        arr.push_back({
            {"date",   bar.date},
            {"open",   bar.open},
            {"high",   bar.high},
            {"low",    bar.low},
            {"close",  bar.close},
            {"volume", bar.volume}
        });
    }
    return arr;
}

/*
 * generate_sample_data — 生成测试用的模拟 K 线数据
 *
 * 使用随机游走（Random Walk）模型模拟价格：
 * 每天的收益率是一个正态分布的随机数。
 * 这是金融学中最基本的价格模型（几何布朗运动的离散版本）。
 *
 * 随机数引擎 std::mt19937：
 * Mersenne Twister 算法，生成高质量伪随机数。
 * 名字中的 19937 是算法的周期长度（非常大的质数）。
 *
 * 正态分布 std::normal_distribution：
 * 均值 0，标准差 daily_volatility
 * 大部分日子涨跌在 ±2σ 之内（约 95%）
 */
std::vector<Bar> DataLoader::generate_sample_data(int days,
                                                    double start_price,
                                                    double daily_volatility) {
    std::vector<Bar> bars;
    bars.reserve(days);

    std::mt19937 rng(42);   // 固定种子 42，确保每次生成相同的数据（可复现）
    std::normal_distribution<double> dist(0.0, daily_volatility);

    double price = start_price;

    for (int i = 0; i < days; ++i) {
        /*
         * 生成日期字符串：2025-01-01, 2025-01-02, ...
         *
         * 🔴 旧实现按「每月 30 天」硬切再 `if (day > 28) day = 28;` 钳一下，
         * 于是每个月的第 28/29/30 天**被压成同一个日期** —— 100 根 bar 只有
         * 94 个不同的日期。按 bar 下标推进的引擎看不见这件事，但组合回测是
         * **按日期**推进的（多个标的的日历要对齐），重复日期会直接把 bar 吞掉。
         *
         * 改成真的日历推进：每月按实际天数走，闰年也算对。
         */
        char date_buf[16];
        int year = 2025, month = 1, day = 1;
        {
            int remaining = i;
            auto days_in = [](int y, int m) {
                static const int t[] = {31,28,31,30,31,30,31,31,30,31,30,31};
                if (m == 2 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0)) return 29;
                return t[m - 1];
            };
            while (remaining > 0) {
                int dim = days_in(year, month);
                if (day + remaining <= dim) { day += remaining; break; }
                remaining -= (dim - day + 1);
                day = 1;
                if (++month > 12) { month = 1; ++year; }
            }
        }
        /*
         * snprintf：格式化字符串到缓冲区
         * %04d：4 位数字，不足补 0（如 2025）
         * %02d：2 位数字，不足补 0（如 01）
         */
        snprintf(date_buf, sizeof(date_buf), "%04d-%02d-%02d", year, month, day);

        // 模拟日内价格波动
        double daily_return = dist(rng);
        double open = price;
        double close = price * (1.0 + daily_return);
        double high = std::max(open, close) * (1.0 + std::abs(dist(rng)) * 0.5);
        double low = std::min(open, close) * (1.0 - std::abs(dist(rng)) * 0.5);
        double volume = 1000000.0 * (1.0 + std::abs(dist(rng)) * 5.0);

        Bar bar;
        bar.date = date_buf;
        bar.open = open;
        bar.high = high;
        bar.low = low;
        bar.close = std::max(close, 0.01);   // 价格不能为负
        bar.volume = volume;

        bars.push_back(bar);
        price = bar.close;   // 下一天的价格从今天的收盘价开始
    }

    return bars;
}

}  // namespace backtest
