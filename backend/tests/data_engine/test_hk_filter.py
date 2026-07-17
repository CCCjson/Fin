"""港股 universe 分类 —— 纯规则测试，零 fixture 零 DB。

样本全部取自 2026-07-17 库里的**真实数据**，不是编的。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_engine.deep_history.hk_filter import (  # noqa: E402
    STOCK_TYPE_ETF,
    STOCK_TYPE_EXCLUDED_BOND_NOTE,
    STOCK_TYPE_EXCLUDED_RMB_COUNTER,
    STOCK_TYPE_STOCK,
    classify_hk_symbol,
)


def test_normal_stocks():
    assert classify_hk_symbol("00700.HK", "腾讯控股") == STOCK_TYPE_STOCK
    assert classify_hk_symbol("09988.HK", "阿里巴巴-SW") == STOCK_TYPE_STOCK
    assert classify_hk_symbol("01737.HK", "华新水泥") == STOCK_TYPE_STOCK


def test_gem_board_is_still_a_stock():
    """创业板 GEM 是 08xxx，5 位归一化后 0 开头 → 正股，别当成人民币柜台排掉。"""
    assert classify_hk_symbol("08083.HK", "中国有赞") == STOCK_TYPE_STOCK


def test_bonds_and_notes_excluded():
    """4xxxx = 债券/票据/结构性产品，抓不到日线，白烧请求。"""
    assert classify_hk_symbol("40939.HK", "SINOCHEM N2611") == STOCK_TYPE_EXCLUDED_BOND_NOTE
    assert classify_hk_symbol("41533.HK", "XAGX金兑-U") == STOCK_TYPE_EXCLUDED_BOND_NOTE
    assert classify_hk_symbol("40921.HK", "SF HLDG N2811") == STOCK_TYPE_EXCLUDED_BOND_NOTE


def test_rmb_counter_duplicates_excluded():
    """人民币柜台是港币柜台的**重复**（89988 阿里 = 09988 阿里）。

    不排掉的话同一家公司抓两遍，选股/胜率统计还会重复计数。
    """
    assert classify_hk_symbol("89988.HK", "阿里巴巴-WR") == STOCK_TYPE_EXCLUDED_RMB_COUNTER
    assert classify_hk_symbol("89888.HK", "百度集团-SWR") == STOCK_TYPE_EXCLUDED_RMB_COUNTER
    assert classify_hk_symbol("89618.HK", "京东集团-SWR") == STOCK_TYPE_EXCLUDED_RMB_COUNTER


def test_rmb_counter_bonds_excluded():
    assert classify_hk_symbol("89021.HK", "国债四一零四-R") == STOCK_TYPE_EXCLUDED_RMB_COUNTER
    assert classify_hk_symbol("89023.HK", "PRC B3106-R") == STOCK_TYPE_EXCLUDED_RMB_COUNTER


def test_rmb_counter_without_r_suffix_still_excluded():
    """8xxxx 里有 2 只名字不带 -R 后缀，但仍是人民币柜台 —— 靠代码段判，不靠名字。"""
    assert classify_hk_symbol("87001.HK", "汇贤产业信托") == STOCK_TYPE_EXCLUDED_RMB_COUNTER
    assert classify_hk_symbol("83168.HK", "恒生人币金ETF") == STOCK_TYPE_EXCLUDED_RMB_COUNTER


def test_etf_by_name():
    assert classify_hk_symbol("03170.HK", "恒生黄金ETF") == STOCK_TYPE_ETF
    assert classify_hk_symbol("03032.HK", "恒生科技ETF") == STOCK_TYPE_ETF
    assert classify_hk_symbol("03136.HK", "恒指ESGETF") == STOCK_TYPE_ETF


def test_reits_are_stocks_not_etfs():
    """⚠️ **这条防的是一次真实的误杀**：REIT 是正经权益资产，领展还是港股大蓝筹。

    若哪天有人给 _ETF_KEYWORDS 加上「基金」「信托」想多抓几只 ETF，这条会红。
    """
    assert classify_hk_symbol("00823.HK", "领展房产基金") == STOCK_TYPE_STOCK
    assert classify_hk_symbol("02778.HK", "冠君产业信托") == STOCK_TYPE_STOCK
    assert classify_hk_symbol("00778.HK", "置富产业信托") == STOCK_TYPE_STOCK
    assert classify_hk_symbol("01881.HK", "富豪产业信托") == STOCK_TYPE_STOCK


def test_etf_named_fund_falls_back_to_stock_and_thats_fine():
    """盈富基金/沛富基金是真 ETF 却被标成 stock —— **已知且可接受**。

    stock 和 etf 都在抓取范围内，标签只影响可读性；承重的区分是「excluded 与否」。
    宁可把 ETF 标成 stock，不可把领展标成 ETF（见上一条）。
    """
    assert classify_hk_symbol("02800.HK", "盈富基金") == STOCK_TYPE_STOCK
    assert classify_hk_symbol("02821.HK", "沛富基金") == STOCK_TYPE_STOCK


def test_none_name_does_not_crash():
    assert classify_hk_symbol("00700.HK", None) == STOCK_TYPE_STOCK
