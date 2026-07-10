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
# `portfolio/risk_analyzer.py` 曾在此列。13.4-2 之后它**不再取裸码**——行业分类
# 改成整表读 `StockInfo.industry`（带后缀的 symbol 直接当主键查），akshare 调用整个
# 消失，`to_bare_code` 自然也没了。守卫见 `tests/acquisition/test_industry.py`。
MULTI_MARKET_CALLSITES = [
    "news_engine/fetcher.py",
    "news_engine/news_scheduler.py",
    "news_engine/analyzer.py",
    "report_engine/chapter_utils.py",
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


def test_risk_analyzer_no_longer_calls_akshare_at_all():
    """13.4-1 那条守卫（先 infer_market_from_symbol 挡住港美股，再调 akshare）已被**取代**。

    13.4-2 实测 `ak.stock_individual_info_em` 打的 `qt/stock/get` 已被东财封杀，
    而 `domestic_akshare` 把「端点死了」当成「IP 死了」，于是每只 A 股持仓白烧
    max_rounds-1 个快代理 IP、结果还是「未知」。行业分类整体改成读
    `StockInfo.industry`，出网调用消失——比「只对 A 股出网」更强。

    这条测试保留下来，是为了记住：如果哪天有人把 akshare 调回来，它得先解释
    为什么那个端点又活了。更细的门禁在 `tests/acquisition/test_industry.py`。
    """
    import ast
    import textwrap

    from portfolio.risk_analyzer import PortfolioRiskAnalyzer

    src = textwrap.dedent(inspect.getsource(PortfolioRiskAnalyzer._calc_industry_concentration))
    tree = ast.parse(src)
    # 只看 AST 标识符：docstring 里刻意记着这段历史，不该被误判
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "domestic_akshare" not in names, "行业集中度又调回 akshare 了"
    assert "stock_individual_info_em" not in names


def test_industry_concentration_marks_hk_and_us_unknown(monkeypatch):
    """港美股在 StockInfo.industry 里没有行业（那是 A 股字段）→ 记「未知」，且不出网。"""
    from portfolio.risk_analyzer import PortfolioRiskAnalyzer

    # 不碰真库：读库路径本身另有测试，这里只验分类逻辑
    monkeypatch.setattr(PortfolioRiskAnalyzer, "_load_industries", staticmethod(lambda syms: {}))

    positions = [
        {"symbol": "00700.HK", "market_value": 1000},
        {"symbol": "BRK.B", "market_value": 1000},
        {"symbol": "AAPL", "market_value": 1000},
    ]
    weights = {p["symbol"]: 1 / 3 for p in positions}
    out = PortfolioRiskAnalyzer()._calc_industry_concentration(positions, weights)

    assert out["industries"] == {"未知": 100.0}
