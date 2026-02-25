#include "pipeline/symbol_mapper.h"
#include <algorithm>

namespace pipeline {

std::string SymbolMapper::code_to_symbol(const std::string& code) {
    if (code.size() != 6) return code;
    // 6 开头 → 上海，其他 → 深圳（与 Python eastmoney_crawler.py 一致）
    if (code[0] == '6') {
        return code + ".SH";
    }
    return code + ".SZ";
}

std::string SymbolMapper::symbol_to_code(const std::string& symbol) {
    auto dot = symbol.find('.');
    if (dot == std::string::npos) return symbol;
    return symbol.substr(0, dot);
}

std::string SymbolMapper::build_secid(const std::string& symbol) {
    std::string code = symbol_to_code(symbol);
    int market = get_market_id(symbol);
    return std::to_string(market) + "." + code;
}

int SymbolMapper::get_market_id(const std::string& symbol) {
    // .SH → market 1（上海），.SZ → market 0（深圳）
    if (symbol.size() > 3) {
        std::string suffix = symbol.substr(symbol.size() - 3);
        if (suffix == ".SH") return 1;
    }
    return 0;
}

bool SymbolMapper::is_valid_code(const std::string& code) {
    if (code.size() != 6) return false;
    return std::all_of(code.begin(), code.end(), ::isdigit);
}

}  // namespace pipeline
