"""13.4-1 行为基线：stock_pools 的成分股代码补后缀。

`data_engine/stock_pools.py` 曾自带一份弱化的 `add_exchange_suffix`（1 位前缀、
无 6 位校验、非法码**静默返回原样**），现改用 `common.market` 的真源。

拿全库 5191 只真实 A 股裸码跑过等价性扫描：**0 处不一致**。唯一的行为变化是
ETF 码（159xxx / 51xxxx / 56xxxx / 588xxx）——旧版静默返回**没有后缀的裸码**
（`"159001"`），让坏 symbol 一路漂到下游；新版抛 ValueError，调用点丢弃该行并留痕。
"""
import pytest

from common.market import add_exchange_suffix
from data_engine import stock_pools

pytestmark = pytest.mark.baseline


def test_stock_pools_no_longer_defines_its_own():
    """真源在 common.market，别再在这儿写第二份。"""
    import inspect
    src = inspect.getsource(stock_pools)
    assert "def add_exchange_suffix" not in src
    assert stock_pools.add_exchange_suffix is add_exchange_suffix


# ── 全库扫描证实的等价段（旧版与新版完全一致）───────────────────────────────

@pytest.mark.parametrize("code,expected", [
    ("600000", "600000.SH"),     # 沪主板
    ("601398", "601398.SH"),
    ("688001", "688001.SH"),     # 科创板
    ("000001", "000001.SZ"),     # 深主板
    ("300750", "300750.SZ"),     # 创业板
    ("301158", "301158.SZ"),
    ("302132", "302132.SZ"),     # 后开的创业板段
    ("430047", "430047.BJ"),     # 北交所
    ("920002", "920002.BJ"),
    ("900901", "900901.SH"),     # 沪B
    ("200011", "200011.SZ"),     # 深B
])
def test_suffix_for_real_a_share_codes(code, expected):
    assert add_exchange_suffix(code) == expected


def test_idempotent_on_already_suffixed():
    """成分股接口偶尔直接给带后缀的码。两版都幂等。"""
    assert add_exchange_suffix("600000.SH") == "600000.SH"
    assert add_exchange_suffix("00700.HK") == "00700.HK"


def test_index_code_needs_asset_index():
    """000001 作个股是平安银行(.SZ)，作指数是上证指数(.SH)——光看代码无解。"""
    assert add_exchange_suffix("000001") == "000001.SZ"
    assert add_exchange_suffix("000001", asset="index") == "000001.SH"
    assert add_exchange_suffix("000300", asset="index") == "000300.SH"


# ── 唯一的行为变化：脏码不再静默通过 ──────────────────────────────────────

@pytest.mark.parametrize("etf_code", ["159001", "512000", "588000", "563000"])
def test_etf_codes_now_raise_instead_of_returning_bare_code(etf_code):
    """旧版返回没有后缀的裸码（坏 symbol 一路漂到下游），新版当场炸。

    A股 ETF 已于 2026-07-09 全面停用，成分股里出现 ETF 码就是脏数据。
    """
    with pytest.raises(ValueError):
        add_exchange_suffix(etf_code)


@pytest.mark.parametrize("bad", ["", "60000", "6000000", "abcdef", "60000X"])
def test_malformed_codes_raise(bad):
    with pytest.raises(ValueError):
        add_exchange_suffix(bad)


# ── 调用点：脏行被丢弃，不炸整批 ───────────────────────────────────────────

def _fake_df(rows):
    import pandas as pd
    return pd.DataFrame(rows, columns=["代码", "名称"])


@pytest.fixture
def fake_akshare(monkeypatch):
    """`from net import domestic_akshare` 是**函数体内延迟 import**——必须打
    `net` 模块的属性，打 `stock_pools` 的没用（它执行时才去 net 拿）。
    """
    import net

    holder = {}

    def _fake(fn, **kwargs):
        return holder["df"]

    monkeypatch.setattr(net, "domestic_akshare", _fake)
    stock_pools._cache.clear()
    yield holder
    stock_pools._cache.clear()


def test_index_constituents_skips_dirty_rows_instead_of_crashing(fake_akshare):
    """一行脏码不该让整个成分股批次返回空（旧版是静默塞个无后缀裸码进去）。"""
    fake_akshare["df"] = _fake_df([
        ["600000", "浦发银行"],
        ["159001", "某ETF"],        # 脏行
        ["000001", "平安银行"],
    ])
    out = stock_pools.fetch_index_constituents("000300")
    assert [r["symbol"] for r in out] == ["600000.SH", "000001.SZ"]


def test_industry_constituents_skips_dirty_rows(fake_akshare):
    fake_akshare["df"] = _fake_df([
        ["512000", "某ETF"],        # 脏行
        ["300750", "宁德时代"],
    ])
    out = stock_pools.fetch_industry_stocks("电池")
    assert [r["symbol"] for r in out] == ["300750.SZ"]


def test_all_clean_rows_survive(fake_akshare):
    fake_akshare["df"] = _fake_df([["600000", "浦发银行"], ["300750", "宁德时代"]])
    out = stock_pools.fetch_index_constituents("000016")
    assert len(out) == 2
