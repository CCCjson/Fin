/*
 * data_loader.h — 数据加载器
 *
 * 负责把外部数据（JSON 格式）转换为我们的 Bar 结构体。
 *
 * 数据流：
 * Python FastAPI 从 DataEngine 获取股票数据 → JSON → POST 到 C++ 服务
 * → DataLoader 解析 JSON → 返回 vector<Bar>
 *
 * 这个模块也支持从 CSV 文件加载数据（用于测试）。
 */

#pragma once

#include "types.h"
#include <vector>
#include <string>
#include <nlohmann/json.hpp>    // JSON 库

namespace backtest {

/*
 * DataLoader — 数据加载器（静态工具类）
 *
 * 提供两种加载方式：
 * 1. 从 JSON 字符串/对象加载（API 使用）
 * 2. 从 CSV 文件加载（测试使用）
 */
class DataLoader {
public:
    /*
     * from_json — 从 JSON 数组加载 K 线数据
     *
     * JSON 格式：
     * [
     *   {"date": "2025-01-01", "open": 100.0, "high": 105.0, ...},
     *   {"date": "2025-01-02", "open": 102.0, "high": 106.0, ...},
     *   ...
     * ]
     *
     * nlohmann::json 是 C++ 最流行的 JSON 库，header-only，使用简单。
     */
    static std::vector<Bar> from_json(const nlohmann::json& j);

    /*
     * from_csv — 从 CSV 文件加载 K 线数据
     *
     * CSV 格式：
     * date,open,high,low,close,volume
     * 2025-01-01,100.0,105.0,98.0,103.0,1000000
     * ...
     *
     * 第一行是表头，会被跳过。
     */
    static std::vector<Bar> from_csv(const std::string& filepath);

    /*
     * to_json — 把 Bar 数组转成 JSON（用于 API 返回）
     */
    static nlohmann::json to_json(const std::vector<Bar>& bars);

    /*
     * generate_sample_data — 生成测试用的随机 K 线数据
     *
     * 用于测试，生成指定天数的模拟数据。
     * 价格从 start_price 开始，每天随机波动。
     */
    static std::vector<Bar> generate_sample_data(
        int days,
        double start_price = 100.0,
        double daily_volatility = 0.02
    );
};

}  // namespace backtest
