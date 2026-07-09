"""common/market.py 的 symbol 形态转换 —— 全项目唯一真源，行为必须锁死。

规则表不是拍脑袋写的：是拿 market.db 里 6594 行真实 A股反推出来的。
起草时按「300/301」枚举 3 位段，唯一那只 302132（中航成飞）就被判成非法。
文件末尾的 parity 测试就是干这个的——让真实数据当裁判。
"""
import pytest

from common.market import add_exchange_suffix, to_bare_code, to_yf_symbol


class TestToBareCode:
    @pytest.mark.parametrize("symbol, expected", [
        ("600519.SH", "600519"),
        ("000001.SZ", "000001"),
        ("830799.BJ", "830799"),
        ("00700.HK", "00700"),      # 港股保前导零
        ("AAPL", "AAPL"),           # 美股无后缀
        ("  600519.SH  ", "600519"),
        ("600519.sh", "600519"),    # 后缀大小写不敏感
    ])
    def test_strips_known_suffix(self, symbol, expected):
        assert to_bare_code(symbol) == expected

    def test_does_not_split_us_ticker_with_dot(self):
        """美股 ticker 可以带点。裸 split('.')[0] 会把 BRK.B 砍成 BRK。"""
        assert to_bare_code("BRK.B") == "BRK.B"
        assert to_bare_code("BF.A") == "BF.A"


class TestAddExchangeSuffixStock:
    @pytest.mark.parametrize("code, expected", [
        ("600519", "600519.SH"),    # 沪主板
        ("601398", "601398.SH"),
        ("603288", "603288.SH"),
        ("605499", "605499.SH"),
        ("688981", "688981.SH"),    # 科创板
        ("689009", "689009.SH"),    # 九号公司 —— 库里唯一的 689 段
        ("000001", "000001.SZ"),    # 平安银行（同码歧义，见 index 档）
        ("002415", "002415.SZ"),
        ("003816", "003816.SZ"),
        ("300750", "300750.SZ"),    # 创业板
        ("301029", "301029.SZ"),
        ("302132", "302132.SZ"),    # 中航成飞 —— 库里唯一的 302 段
    ])
    def test_mainstream_segments(self, code, expected):
        assert add_exchange_suffix(code) == expected

    @pytest.mark.parametrize("code, expected", [
        ("830799", "830799.BJ"),
        ("831010", "831010.BJ"),
        ("430047", "430047.BJ"),
        ("871981", "871981.BJ"),
        ("889999", "889999.BJ"),
        ("920002", "920002.BJ"),    # 北交所新段
        ("920819", "920819.BJ"),
    ])
    def test_beijing_exchange(self, code, expected):
        """北交所。现有 a_share.py 内联规则会把这些全拼成 .SZ（bug B）。"""
        assert add_exchange_suffix(code) == expected

    def test_long_prefix_wins_over_short(self):
        """900(沪B) 必须先于 92(北交所) 命中，否则 920xxx 被吞成 .SH。"""
        assert add_exchange_suffix("900901") == "900901.SH"   # 沪B
        assert add_exchange_suffix("920002") == "920002.BJ"   # 北交所
        assert add_exchange_suffix("200011") == "200011.SZ"   # 深B


class TestAddExchangeSuffixIndex:
    def test_same_code_different_asset_different_exchange(self):
        """本函数存在的根本理由：000001 同码不同所，光看代码无解。"""
        assert add_exchange_suffix("000001", asset="stock") == "000001.SZ"  # 平安银行
        assert add_exchange_suffix("000001", asset="index") == "000001.SH"  # 上证指数

    @pytest.mark.parametrize("code, expected", [
        ("000001", "000001.SH"),    # 上证指数
        ("000016", "000016.SH"),    # 上证50
        ("000300", "000300.SH"),    # 沪深300
        ("000688", "000688.SH"),    # 科创50
        ("000852", "000852.SH"),    # 中证1000
        ("000905", "000905.SH"),    # 中证500
        ("399001", "399001.SZ"),    # 深证成指
        ("399006", "399006.SZ"),    # 创业板指
        ("399673", "399673.SZ"),    # 创业板50
    ])
    def test_all_eleven_live_indices(self, code, expected):
        assert add_exchange_suffix(code, asset="index") == expected

    def test_stock_segment_rejected_in_index_mode(self):
        with pytest.raises(ValueError, match="指数"):
            add_exchange_suffix("600519", asset="index")


class TestAddExchangeSuffixIdempotenceAndErrors:
    @pytest.mark.parametrize("symbol", ["600519.SH", "000001.SZ", "830799.BJ", "00700.HK"])
    def test_already_suffixed_returned_unchanged(self, symbol):
        assert add_exchange_suffix(symbol) == symbol

    @pytest.mark.parametrize("code", [
        "510300",   # 旧 A股 ETF 段（沪）—— ETF 已于 2026-07-09 全面停用
        "159919",   # 旧 A股 ETF 段（深）
        "512880",
    ])
    def test_dead_etf_segments_rejected(self, code):
        with pytest.raises(ValueError):
            add_exchange_suffix(code)

    @pytest.mark.parametrize("code", [
        "AAPL",     # 美股 ticker 不该进这个函数
        "00700",    # 港股 5 位裸码
        "",
        "12345",    # 5 位
        "1234567",  # 7 位
        "60051X",   # 非纯数字
    ])
    def test_non_a_share_bare_code_rejected(self, code):
        with pytest.raises(ValueError, match="6 位纯数字"):
            add_exchange_suffix(code)

    def test_unknown_six_digit_segment_rejected(self):
        with pytest.raises(ValueError, match="未知"):
            add_exchange_suffix("999999")


class TestToYfSymbol:
    @pytest.mark.parametrize("symbol, expected", [
        ("00700.HK", "0700.HK"),    # 5 位 → 4 位
        ("09988.HK", "9988.HK"),
        ("0700.HK", "0700.HK"),     # 已是 4 位，不动
        ("600519.SH", "600519.SH"), # A股原样
        ("AAPL", "AAPL"),           # 美股原样
        ("BRK.B", "BRK.B"),
    ])
    def test_conversion(self, symbol, expected):
        assert to_yf_symbol(symbol) == expected


@pytest.mark.integration
def test_parity_against_real_stock_info():
    """最硬的验收：拿库里 5190 只活跃 A股 + 11 只指数，断言规则表逐字复现其 symbol。

    起草规则表时就是这条测试裁出了 302xxx 漏洞。标 integration 是因为它依赖真实库。
    """
    import sqlite3
    from pathlib import Path

    db = Path(__file__).resolve().parents[2] / "data" / "market.db"
    if not db.exists():
        pytest.skip(f"真实库不存在: {db}")

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, stock_type FROM stock_info "
            "WHERE market = 'a_share' AND is_active = 1 AND stock_type IN ('stock', 'index')"
        ).fetchall()
    finally:
        con.close()

    assert len(rows) > 5000, f"库里活跃 A股太少（{len(rows)}），样本不足以验收"

    mismatches = []
    for symbol, stock_type in rows:
        asset = "index" if stock_type == "index" else "stock"
        try:
            rebuilt = add_exchange_suffix(to_bare_code(symbol), asset=asset)
        except ValueError as exc:
            mismatches.append(f"{symbol}({stock_type}): {exc}")
            continue
        if rebuilt != symbol:
            mismatches.append(f"{symbol}({stock_type}): 重建为 {rebuilt}")

    assert not mismatches, f"{len(mismatches)}/{len(rows)} 只对不上:\n" + "\n".join(mismatches[:20])
