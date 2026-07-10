"""行业分类：取数（东财 clist f100）+ 回填 + 风控读库零出网。

背景：`ak.stock_individual_info_em` 打的 `qt/stock/get` 已被东财封杀
（2026-07-10 实测），而 `domestic_akshare` 把「端点死了」当成「IP 死了」，
于是 `risk_analyzer` 逐只持仓白烧 max_rounds-1 个快代理 IP，结果还是「未知」。
"""
import pytest

from acquisition.markets.industry import fetch_a_share_industry_map
from net.domestic import ProxyQuotaExhaustedError, ProxyRetriesExhaustedError

pytestmark = pytest.mark.baseline


def _page(rows, total):
    return {"rc": 0, "data": {"total": total, "diff": rows}}


class _FakeCrawler:
    """按页返回预置数据。

    ⚠️ 忠实建模真实契约：DOMESTIC 通道下 `get_json` **换满 IP 后是抛
    `ProxyRetriesExhaustedError`，不是返回 None**。第一版假对象返回 None，
    于是「跳过失败页」那段分支从未被真正执行过——真跑全市场回填才炸出来。
    """

    def __init__(self, pages, dead_pages=(), quota_dead_pages=()):
        self.pages = pages
        self.dead_pages = set(dead_pages)                # 换满 IP 仍全败 → 跳过该页
        self.quota_dead_pages = set(quota_dead_pages)    # 一个 IP 都取不到 → 整体放弃
        self.seen_params = []

    def get_json(self, url, *, params=None, headers=None):
        self.seen_params.append(params)
        pn = int(params["pn"])
        if pn in self.quota_dead_pages:
            raise ProxyQuotaExhaustedError(f"额度耗尽 @pn={pn}")
        if pn in self.dead_pages:
            raise ProxyRetriesExhaustedError(f"换满 IP 仍全败 @pn={pn}")
        return self.pages.get(pn)


@pytest.fixture
def patch_crawler(monkeypatch):
    def _install(pages, dead_pages=(), quota_dead_pages=()):
        fake = _FakeCrawler(pages, dead_pages, quota_dead_pages)
        monkeypatch.setattr("acquisition.markets.industry.BaseCrawler",
                            lambda **kw: fake)
        return fake
    return _install


# ── 取数 ────────────────────────────────────────────────────────────────────

def test_parses_f100_into_symbol_industry_map(patch_crawler):
    patch_crawler({1: _page([
        {"f12": "600000", "f14": "浦发银行", "f100": "银行Ⅱ"},
        {"f12": "000001", "f14": "平安银行", "f100": "银行Ⅱ"},
        {"f12": "300750", "f14": "宁德时代", "f100": "电池"},
        {"f12": "688981", "f14": "中芯国际", "f100": "半导体"},
    ], total=4)})

    m = fetch_a_share_industry_map()
    assert m == {
        "600000.SH": "银行Ⅱ",
        "000001.SZ": "银行Ⅱ",
        "300750.SZ": "电池",
        "688981.SH": "半导体",
    }


def test_skips_stocks_without_industry(patch_crawler):
    """东财给退市/无行业的票填 '-'，别把它当行业名落库。"""
    patch_crawler({1: _page([
        {"f12": "600000", "f14": "浦发银行", "f100": "银行Ⅱ"},
        {"f12": "600001", "f14": "邯郸钢铁", "f100": "-"},
        {"f12": "600002", "f14": "齐鲁石化", "f100": ""},
        {"f12": "600003", "f14": "某某", "f100": None},
    ], total=4)})

    assert fetch_a_share_industry_map() == {"600000.SH": "银行Ⅱ"}


def test_skips_unparseable_code_without_killing_the_batch(patch_crawler):
    """单只脏码（ETF 段/权证）抛 ValueError，不该炸掉整张表。"""
    patch_crawler({1: _page([
        {"f12": "510300", "f14": "某ETF", "f100": "指数"},      # ETF 段 → ValueError
        {"f12": "600000", "f14": "浦发银行", "f100": "银行Ⅱ"},
        {"f12": "abc", "f14": "脏码", "f100": "垃圾"},
    ], total=3)})

    assert fetch_a_share_industry_map() == {"600000.SH": "银行Ⅱ"}


def test_dead_page_is_skipped_not_fatal(patch_crawler):
    """快代理池里死 IP 多，一页换满 IP 仍失败不该拖垮全市场回补。"""
    pages = {
        1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=300),
        3: _page([{"f12": "600003", "f14": "c", "f100": "钢铁"}], total=300),
    }
    fake = patch_crawler(pages, dead_pages=[2])

    m = fetch_a_share_industry_map(page_size=100)
    assert m == {"600000.SH": "银行Ⅱ", "600003.SH": "钢铁"}
    assert [int(p["pn"]) for p in fake.seen_params] == [1, 2, 3], "第 2 页失败后要继续第 3 页"


def test_first_page_failure_does_not_abort(patch_crawler):
    """第 1 页失败时 total 还未知——不能因此判定「翻到底了」。"""
    pages = {2: _page([{"f12": "600002", "f14": "b", "f100": "钢铁"}], total=200)}
    fake = patch_crawler(pages, dead_pages=[1])

    assert fetch_a_share_industry_map(page_size=100) == {"600002.SH": "钢铁"}
    assert [int(p["pn"]) for p in fake.seen_params] == [1, 2]


def test_quota_exhausted_aborts_the_whole_job(patch_crawler):
    """「一个 IP 都取不到」和「这页换满 IP 仍失败」是两回事：前者继续翻页毫无意义。"""
    pages = {1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=300)}
    fake = patch_crawler(pages, quota_dead_pages=[2])

    with pytest.raises(ProxyQuotaExhaustedError):
        fetch_a_share_industry_map(page_size=100)
    assert [int(p["pn"]) for p in fake.seen_params] == [1, 2], "额度耗尽后不该再翻第 3 页"


def test_dead_pool_aborts_after_consecutive_failures(patch_crawler):
    """整个池子挂了 → 别把 200 页 × 8 个 IP 全烧一遍。"""
    pages = {1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=10_000)}
    fake = patch_crawler(pages, dead_pages=range(2, 50))

    m = fetch_a_share_industry_map(page_size=100)
    assert m == {"600000.SH": "银行Ⅱ"}, "拿到的部分要留下"
    pages_tried = [int(p["pn"]) for p in fake.seen_params]
    assert pages_tried == [1, 2, 3, 4, 5], f"连续 4 页失败就该中止，实际翻了 {pages_tried}"


def test_stops_at_total(patch_crawler):
    fake = patch_crawler({1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=1)})
    fetch_a_share_industry_map(page_size=100)
    assert len(fake.seen_params) == 1, "total 已够就不该再翻页"


def test_requests_f100_field(patch_crawler):
    fake = patch_crawler({1: _page([], total=0)})
    fetch_a_share_industry_map()
    assert "f100" in fake.seen_params[0]["fields"]


# ── 风控读库，零出网 ─────────────────────────────────────────────────────────

def _risk_analyzer_tree():
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "portfolio" / "risk_analyzer.py"
    return ast.parse(src.read_text(encoding="utf-8"))


def test_risk_analyzer_never_touches_the_dead_endpoint():
    """`stock_individual_info_em` 打的 qt/stock/get 已被东财封杀，且每调一次白烧 IP。

    结构门禁：只看 AST 里的标识符（docstring 里记着这段历史，是有意为之）。
    比「monkeypatch 一个函数再断言它没被调」硬——那种测试在符号已经不被 import
    时会假绿。
    """
    import ast

    tree = _risk_analyzer_tree()
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "stock_individual_info_em" not in names, "风控又调回那个死端点了"


def test_industry_path_does_not_go_online():
    """行业那两个函数体内不许有 akshare / net / acquisition 的 import 或调用。

    （Beta 计算取沪深300 日线是**合法**出网，不在门禁范围内——所以只钉行业路径。）
    """
    import ast

    tree = _risk_analyzer_tree()
    targets = {"_calc_industry_concentration", "_load_industries"}
    checked = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in targets:
            continue
        checked.add(node.name)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                mods = {a.name.split(".")[0] for a in sub.names}
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                mods = {sub.module.split(".")[0]}
            else:
                continue
            bad = mods & {"akshare", "net", "acquisition"}
            assert not bad, f"{node.name} 里 import 了出网模块 {bad}——行业只读库"

    assert checked == targets, f"没找到要检查的函数：{targets - checked}"


def test_risk_analyzer_industry_reads_db_only(monkeypatch):
    """行业分类只读库；库里没有的（含港美股）记「未知」。"""
    import portfolio.risk_analyzer as ra

    monkeypatch.setattr(ra.PortfolioRiskAnalyzer, "_load_industries",
                        staticmethod(lambda syms: {"600000.SH": "银行Ⅱ", "000001.SZ": "银行Ⅱ"}))

    positions = [{"symbol": "600000.SH"}, {"symbol": "000001.SZ"},
                 {"symbol": "AAPL"}, {"symbol": "00700.HK"}]
    weights = {"600000.SH": 0.4, "000001.SZ": 0.3, "AAPL": 0.2, "00700.HK": 0.1}

    out = ra.PortfolioRiskAnalyzer()._calc_industry_concentration(positions, weights)
    assert out["industries"]["银行Ⅱ"] == 70.0
    assert out["industries"]["未知"] == 30.0, "库里没有的（含港美股）记未知"


def test_caller_supplied_industry_wins(monkeypatch):
    import portfolio.risk_analyzer as ra

    monkeypatch.setattr(ra.PortfolioRiskAnalyzer, "_load_industries",
                        staticmethod(lambda syms: {"600000.SH": "银行Ⅱ"}))
    out = ra.PortfolioRiskAnalyzer()._calc_industry_concentration(
        [{"symbol": "600000.SH", "industry": "自定义"}], {"600000.SH": 1.0})
    assert out["industries"] == {"自定义": 100.0}
