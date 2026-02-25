#include "pipeline/kline_parser.h"
#include <sstream>
#include <stdexcept>

namespace pipeline {

std::vector<Bar> KlineParser::parse(const std::string& json_str,
                                     const std::string& symbol,
                                     const std::string& market) {
    std::vector<Bar> bars;

    nlohmann::json root;
    try {
        root = nlohmann::json::parse(json_str);
    } catch (const nlohmann::json::parse_error&) {
        return bars;  // 解析失败返回空
    }

    // JSON 路径: data.klines[]
    if (!root.contains("data") || root["data"].is_null()) return bars;
    auto& data = root["data"];
    if (!data.contains("klines") || !data["klines"].is_array()) return bars;

    auto& klines = data["klines"];
    bars.reserve(klines.size());

    for (const auto& line : klines) {
        if (!line.is_string()) continue;
        try {
            bars.push_back(parse_line(line.get<std::string>(), symbol, market));
        } catch (...) {
            // 跳过解析失败的行
        }
    }

    return bars;
}

Bar KlineParser::parse_line(const std::string& line,
                             const std::string& symbol,
                             const std::string& market) {
    // 字段顺序（与 Python eastmoney_crawler.py:parse_kline_data 一致）:
    // 0: date, 1: open, 2: close, 3: high, 4: low,
    // 5: volume, 6: amount, 7: amplitude, 8: change_pct, 9: change, 10: turnover
    std::vector<std::string> parts;
    std::istringstream ss(line);
    std::string token;
    while (std::getline(ss, token, ',')) {
        parts.push_back(token);
    }

    if (parts.size() < 11) {
        throw std::runtime_error("Kline line has fewer than 11 fields");
    }

    Bar bar;
    bar.symbol = symbol;
    bar.market = market;
    bar.date = parts[0];
    bar.open = std::stod(parts[1]);
    bar.close = std::stod(parts[2]);   // index 2 = close（不是 high！）
    bar.high = std::stod(parts[3]);
    bar.low = std::stod(parts[4]);
    bar.volume = std::stod(parts[5]);
    bar.amount = std::stod(parts[6]);
    bar.turnover = std::stod(parts[10]);

    return bar;
}

}  // namespace pipeline
