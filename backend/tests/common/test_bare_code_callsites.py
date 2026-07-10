"""13.4-1 行为基线：多市场取裸码的调用点不许回退成裸 split。

`symbol.split(".")[0]` 在 A 股域内没问题，但美股 ticker 可以带点——`BRK.B` 会被
砍成 `BRK`，喂给 Finnhub 就查错公司。`common.market.to_bare_code` 只剥**已知后缀**
（`.SH/.SZ/.BJ/.HK`），美股原样返回。

这里钉住那几个真的会收到美股 symbol 的调用点。A 股域内的 split 收益为零，没动。
"""
import inspect
import pathlib

import pytest

from common.market import to_bare_code

pytestmark = pytest.mark.baseline

_BACKEND = pathlib.Path(__file__).resolve().parents[2]

# 会收到多市场 symbol 的取裸码点。web_searcher 的东财 secid 拼装不在此列
# ——它是 wire 边界（`1.600000`），且非 SH/SZ 直接 continue，BRK.B 到不了那儿。
MULTI_MARKET_CALLSITES = [
    "news_engine/fetcher.py",
    "news_engine/news_scheduler.py",
    "news_engine/analyzer.py",
    "report_engine/chapter_utils.py",
    "portfolio/risk_analyzer.py",
]


def test_us_ticker_with_dot_survives():
    assert to_bare_code("BRK.B") == "BRK.B"
    assert to_bare_code("BF.B") == "BF.B"
    assert "BRK.B".split(".")[0] == "BRK", "这就是被修掉的那个行为"


def test_known_suffixes_are_stripped():
    assert to_bare_code("600519.SH") == "600519"
    assert to_bare_code("00700.HK") == "00700"      # 港股保前导零
    assert to_bare_code("430047.BJ") == "430047"
    assert to_bare_code("AAPL") == "AAPL"


@pytest.mark.parametrize("path", MULTI_MARKET_CALLSITES)
def test_multi_market_callsites_do_not_use_naive_split(path):
    src = (_BACKEND / path).read_text()
    assert 'split(".")[0]' not in src, f"{path} 又用回裸 split 了"
    assert "to_bare_code" in src


def test_risk_analyzer_skips_non_a_share_before_calling_akshare():
    """ak.stock_individual_info_em 只认 A 股。港美股持仓此前每只白跑一次出网。"""
    from portfolio.risk_analyzer import PortfolioRiskAnalyzer

    src = inspect.getsource(PortfolioRiskAnalyzer._calc_industry_concentration)
    assert "infer_market_from_symbol" in src
    # 守卫必须在**真实调用**之前（找调用点而非任意出现——注释里也提到了这个函数名）
    assert src.index("infer_market_from_symbol") < src.index("domestic_akshare(ak.stock_individual_info_em")


def test_industry_concentration_never_calls_akshare_for_hk_or_us(monkeypatch):
    import net
    from portfolio.risk_analyzer import PortfolioRiskAnalyzer

    called = []
    monkeypatch.setattr(net, "domestic_akshare", lambda fn, **kw: called.append(kw) or None)

    positions = [
        {"symbol": "00700.HK", "market_value": 1000},
        {"symbol": "BRK.B", "market_value": 1000},
        {"symbol": "AAPL", "market_value": 1000},
    ]
    weights = {p["symbol"]: 1 / 3 for p in positions}
    out = PortfolioRiskAnalyzer()._calc_industry_concentration(positions, weights)

    assert called == [], f"不该为港美股调 A 股接口，实际调了 {called}"
    assert out["industries"] == {"未知": 100.0}
