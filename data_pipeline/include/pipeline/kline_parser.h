#pragma once

#include "pipeline/types.h"
#include <string>
#include <vector>
#include <nlohmann/json.hpp>

namespace pipeline {

class KlineParser {
public:
    // 解析 EastMoney JSON 响应 → vector<Bar>
    // JSON 路径: data.klines[]，每项是逗号分隔字符串
    // 字段顺序: date, open, close, high, low, volume, amount, amplitude, change_pct, change, turnover
    // 注意: index 2 是 close 不是 high！与 Python parse_kline_data() 一致
    static std::vector<Bar> parse(const std::string& json_str,
                                  const std::string& symbol,
                                  const std::string& market = "a_share");

    // 解析单行 kline 字符串
    static Bar parse_line(const std::string& line,
                          const std::string& symbol,
                          const std::string& market = "a_share");
};

}  // namespace pipeline
