#include <gtest/gtest.h>
#include "pipeline/kline_parser.h"

using namespace pipeline;

TEST(KlineParser, ParseLine) {
    // 字段顺序: date, open, close, high, low, volume, amount, amplitude, change_pct, change, turnover
    std::string line = "2025-01-02,10.50,10.80,11.00,10.30,1000000,10500000,6.67,2.86,0.30,1.25";
    Bar bar = KlineParser::parse_line(line, "000001.SZ");

    EXPECT_EQ(bar.symbol, "000001.SZ");
    EXPECT_EQ(bar.market, "a_share");
    EXPECT_EQ(bar.date, "2025-01-02");
    EXPECT_DOUBLE_EQ(bar.open, 10.50);
    EXPECT_DOUBLE_EQ(bar.close, 10.80);   // index 2 = close
    EXPECT_DOUBLE_EQ(bar.high, 11.00);    // index 3 = high
    EXPECT_DOUBLE_EQ(bar.low, 10.30);     // index 4 = low
    EXPECT_DOUBLE_EQ(bar.volume, 1000000);
    EXPECT_DOUBLE_EQ(bar.amount, 10500000);
    EXPECT_DOUBLE_EQ(bar.turnover, 1.25);
}

TEST(KlineParser, ParseFullJson) {
    std::string json = R"({
        "data": {
            "code": "000001",
            "klines": [
                "2025-01-02,10.50,10.80,11.00,10.30,1000000,10500000,6.67,2.86,0.30,1.25",
                "2025-01-03,10.80,11.00,11.20,10.70,1200000,13200000,4.63,1.85,0.20,1.50"
            ]
        }
    })";

    auto bars = KlineParser::parse(json, "000001.SZ");
    ASSERT_EQ(bars.size(), 2);
    EXPECT_EQ(bars[0].date, "2025-01-02");
    EXPECT_DOUBLE_EQ(bars[0].close, 10.80);
    EXPECT_EQ(bars[1].date, "2025-01-03");
    EXPECT_DOUBLE_EQ(bars[1].close, 11.00);
}

TEST(KlineParser, ParseEmptyData) {
    std::string json = R"({"data": null})";
    auto bars = KlineParser::parse(json, "000001.SZ");
    EXPECT_TRUE(bars.empty());
}

TEST(KlineParser, ParseNoKlines) {
    std::string json = R"({"data": {"code": "000001"}})";
    auto bars = KlineParser::parse(json, "000001.SZ");
    EXPECT_TRUE(bars.empty());
}

TEST(KlineParser, ParseInvalidJson) {
    auto bars = KlineParser::parse("not json", "000001.SZ");
    EXPECT_TRUE(bars.empty());
}

TEST(KlineParser, ParseLineTooFewFields) {
    std::string line = "2025-01-02,10.50,10.80";
    EXPECT_THROW(KlineParser::parse_line(line, "000001.SZ"), std::runtime_error);
}
