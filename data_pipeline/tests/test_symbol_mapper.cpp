#include <gtest/gtest.h>
#include "pipeline/symbol_mapper.h"

using namespace pipeline;

TEST(SymbolMapper, CodeToSymbol) {
    EXPECT_EQ(SymbolMapper::code_to_symbol("000001"), "000001.SZ");
    EXPECT_EQ(SymbolMapper::code_to_symbol("300750"), "300750.SZ");
    EXPECT_EQ(SymbolMapper::code_to_symbol("600000"), "600000.SH");
    EXPECT_EQ(SymbolMapper::code_to_symbol("688001"), "688001.SH");
}

TEST(SymbolMapper, SymbolToCode) {
    EXPECT_EQ(SymbolMapper::symbol_to_code("000001.SZ"), "000001");
    EXPECT_EQ(SymbolMapper::symbol_to_code("600000.SH"), "600000");
    EXPECT_EQ(SymbolMapper::symbol_to_code("ABC"), "ABC");  // no dot
}

TEST(SymbolMapper, BuildSecid) {
    // .SZ → market 0, .SH → market 1
    EXPECT_EQ(SymbolMapper::build_secid("000001.SZ"), "0.000001");
    EXPECT_EQ(SymbolMapper::build_secid("600000.SH"), "1.600000");
}

TEST(SymbolMapper, GetMarketId) {
    EXPECT_EQ(SymbolMapper::get_market_id("000001.SZ"), 0);
    EXPECT_EQ(SymbolMapper::get_market_id("600000.SH"), 1);
}

TEST(SymbolMapper, IsValidCode) {
    EXPECT_TRUE(SymbolMapper::is_valid_code("000001"));
    EXPECT_TRUE(SymbolMapper::is_valid_code("600000"));
    EXPECT_FALSE(SymbolMapper::is_valid_code("12345"));     // too short
    EXPECT_FALSE(SymbolMapper::is_valid_code("1234567"));   // too long
    EXPECT_FALSE(SymbolMapper::is_valid_code("abcdef"));    // not digits
}
