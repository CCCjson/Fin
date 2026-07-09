"""stock_info 回填的回归测试 —— 锁死 exchange 不再被跨市场污染。

历史 bug：init_db 的 exchange 回填按代码前缀猜且不按 market 过滤，港股 5 位码
（`89988.HK` 阿里巴巴）撞上 A股 `symbol LIKE '8%'` 规则被标成 'BJ'，4699 只港股
全部受污染。这里用内存库锁死修复后的行为。
"""
import pytest
from sqlalchemy import create_engine, text

from data_engine.storage.database import backfill_stock_info

# (symbol, market, stock_type, exchange) —— exchange=None 表示回填前为空
_SEED = [
    # A股个股：应按 symbol 后缀回填
    ("600519.SH", "a_share", "stock", None),
    ("000001.SZ", "a_share", "stock", None),
    ("302132.SZ", "a_share", "stock", None),   # 中航成飞，前缀规则会漏的段
    ("830799.BJ", "a_share", "stock", None),   # 北交所
    # A股指数：000001 与上面的平安银行同码不同所，前缀规则无解，后缀规则精确
    ("000001.SH", "a_share", "index", None),
    ("399001.SZ", "a_share", "index", None),
    # 港股：绝不能被打上任何 A股交易所
    ("89988.HK", "hk_stock", "stock", "BJ"),   # 历史污染值，应被清回 NULL
    ("00700.HK", "hk_stock", "stock", "SZ"),   # 历史污染值，应被清回 NULL
    ("04333.HK", "hk_stock", "stock", None),
    # 美股：恒为 NULL
    ("AAPL", "us_stock", "stock", None),
    ("SPY", "us_stock", "etf", None),
]


@pytest.fixture()
def conn():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE stock_info ("
            " symbol TEXT PRIMARY KEY, market TEXT, stock_type TEXT, exchange TEXT)"
        ))
        for symbol, market, stype, exch in _SEED:
            c.execute(
                text("INSERT INTO stock_info VALUES (:s, :m, :t, :e)"),
                {"s": symbol, "m": market, "t": stype, "e": exch},
            )
        yield c


def _exchange_of(conn, symbol):
    return conn.execute(
        text("SELECT exchange FROM stock_info WHERE symbol = :s"), {"s": symbol}
    ).scalar()


def test_a_share_exchange_filled_from_symbol_suffix(conn):
    backfill_stock_info(conn)
    assert _exchange_of(conn, "600519.SH") == "SH"
    assert _exchange_of(conn, "000001.SZ") == "SZ"
    assert _exchange_of(conn, "302132.SZ") == "SZ"
    assert _exchange_of(conn, "830799.BJ") == "BJ"


def test_index_same_code_different_exchange(conn):
    """000001.SH(上证指数) 与 000001.SZ(平安银行) 同码不同所 —— 后缀规则各归各位。"""
    backfill_stock_info(conn)
    assert _exchange_of(conn, "000001.SH") == "SH"
    assert _exchange_of(conn, "000001.SZ") == "SZ"
    assert _exchange_of(conn, "399001.SZ") == "SZ"


def test_hk_stocks_never_get_a_share_exchange(conn):
    """回归 bug：89988.HK 曾被误标 BJ、00700.HK 曾被误标 SZ。"""
    backfill_stock_info(conn)
    for symbol in ("89988.HK", "00700.HK", "04333.HK"):
        assert _exchange_of(conn, symbol) is None, f"{symbol} 的 exchange 应为 NULL"


def test_us_stocks_exchange_stays_null(conn):
    backfill_stock_info(conn)
    assert _exchange_of(conn, "AAPL") is None
    assert _exchange_of(conn, "SPY") is None


def test_stock_type_default_backfilled(conn):
    conn.execute(text("UPDATE stock_info SET stock_type = NULL WHERE symbol = 'AAPL'"))
    backfill_stock_info(conn)
    assert conn.execute(
        text("SELECT stock_type FROM stock_info WHERE symbol = 'AAPL'")
    ).scalar() == "stock"


def test_backfill_is_idempotent(conn):
    backfill_stock_info(conn)
    snapshot = conn.execute(text("SELECT symbol, exchange FROM stock_info ORDER BY symbol")).all()
    backfill_stock_info(conn)
    assert conn.execute(text("SELECT symbol, exchange FROM stock_info ORDER BY symbol")).all() == snapshot
