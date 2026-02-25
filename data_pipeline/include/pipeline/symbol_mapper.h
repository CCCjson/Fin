#pragma once

#include <string>

namespace pipeline {

// 与 Python eastmoney_crawler.py 完全一致的映射逻辑
class SymbolMapper {
public:
    // "000001" → "000001.SZ"  /  "600000" → "600000.SH"
    static std::string code_to_symbol(const std::string& code);

    // "000001.SZ" → "000001"
    static std::string symbol_to_code(const std::string& symbol);

    // "000001.SZ" → "0.000001"  /  "600000.SH" → "1.600000"
    static std::string build_secid(const std::string& symbol);

    // "000001.SZ" → 0 (深圳)  /  "600000.SH" → 1 (上海)
    static int get_market_id(const std::string& symbol);

    // 判断是否是有效的 A 股代码
    static bool is_valid_code(const std::string& code);
};

}  // namespace pipeline
