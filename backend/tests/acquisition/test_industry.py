"""行业分类：取数（东财 clist f100）+ 回填 + 风控读库零出网。

背景：`ak.stock_individual_info_em` 打的 `qt/stock/get` 是**重度 IP 门控**端点
（2026-07-10 实测：新快代理 IP 只有 1/14 能通短字段版，akshare 的 116 字段版 0/14）。
`domestic_akshare` 把「东财拒绝这个 IP」当成「IP 坏了」，于是 `risk_analyzer`
逐只持仓白烧 max_rounds-1 个快代理 IP，结果还是「未知」。
"""
import pytest

from acquisition.markets.industry import _iter_eastmoney, fetch_a_share_industry_map
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

    m = dict(_iter_eastmoney())
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

    assert dict(_iter_eastmoney()) == {"600000.SH": "银行Ⅱ"}


def test_skips_unparseable_code_without_killing_the_batch(patch_crawler):
    """单只脏码（ETF 段/权证）抛 ValueError，不该炸掉整张表。"""
    patch_crawler({1: _page([
        {"f12": "510300", "f14": "某ETF", "f100": "指数"},      # ETF 段 → ValueError
        {"f12": "600000", "f14": "浦发银行", "f100": "银行Ⅱ"},
        {"f12": "abc", "f14": "脏码", "f100": "垃圾"},
    ], total=3)})

    assert dict(_iter_eastmoney()) == {"600000.SH": "银行Ⅱ"}


def test_dead_page_is_skipped_not_fatal(patch_crawler):
    """快代理池里死 IP 多，一页换满 IP 仍失败不该拖垮全市场回补。"""
    pages = {
        1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=300),
        3: _page([{"f12": "600003", "f14": "c", "f100": "钢铁"}], total=300),
    }
    fake = patch_crawler(pages, dead_pages=[2])

    m = dict(_iter_eastmoney(page_size=100))
    assert m == {"600000.SH": "银行Ⅱ", "600003.SH": "钢铁"}
    assert [int(p["pn"]) for p in fake.seen_params] == [1, 2, 3], "第 2 页失败后要继续第 3 页"


def test_first_page_failure_does_not_abort(patch_crawler):
    """第 1 页失败时 total 还未知——不能因此判定「翻到底了」。"""
    pages = {2: _page([{"f12": "600002", "f14": "b", "f100": "钢铁"}], total=200)}
    fake = patch_crawler(pages, dead_pages=[1])

    assert dict(_iter_eastmoney(page_size=100)) == {"600002.SH": "钢铁"}
    assert [int(p["pn"]) for p in fake.seen_params] == [1, 2]


def test_quota_exhausted_aborts_the_whole_job(patch_crawler):
    """「一个 IP 都取不到」和「这页换满 IP 仍失败」是两回事：前者继续翻页毫无意义。"""
    pages = {1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=300)}
    fake = patch_crawler(pages, quota_dead_pages=[2])

    with pytest.raises(ProxyQuotaExhaustedError):
        dict(_iter_eastmoney(page_size=100))
    assert [int(p["pn"]) for p in fake.seen_params] == [1, 2], "额度耗尽后不该再翻第 3 页"


def test_dead_pool_aborts_after_consecutive_failures(patch_crawler):
    """整个池子挂了 → 别把 200 页 × 8 个 IP 全烧一遍。"""
    pages = {1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=10_000)}
    fake = patch_crawler(pages, dead_pages=range(2, 50))

    m = dict(_iter_eastmoney(page_size=100))
    assert m == {"600000.SH": "银行Ⅱ"}, "拿到的部分要留下"
    pages_tried = [int(p["pn"]) for p in fake.seen_params]
    assert pages_tried == [1, 2, 3, 4, 5], f"连续 4 页失败就该中止，实际翻了 {pages_tried}"


def test_stops_at_total(patch_crawler):
    fake = patch_crawler({1: _page([{"f12": "600000", "f14": "a", "f100": "银行Ⅱ"}], total=1)})
    dict(_iter_eastmoney(page_size=100))
    assert len(fake.seen_params) == 1, "total 已够就不该再翻页"


def test_requests_f100_field(patch_crawler):
    fake = patch_crawler({1: _page([], total=0)})
    dict(_iter_eastmoney())
    assert "f100" in fake.seen_params[0]["fields"]


# ── 源链：东财不行就换巨潮 ────────────────────────────────────────────────────

@pytest.fixture
def patch_sources(monkeypatch):
    """替换两个源与探测函数，只测选源逻辑。"""
    calls = []

    def _install(*, em_reachable, em_map=None, cninfo_map=None):
        monkeypatch.setattr("acquisition.markets.industry._eastmoney_reachable",
                            lambda: calls.append("probe") or em_reachable)
        monkeypatch.setattr("acquisition.markets.industry._iter_eastmoney",
                            lambda **kw: calls.append("eastmoney") or iter((em_map or {}).items()))
        monkeypatch.setattr("acquisition.markets.industry._iter_cninfo",
                            lambda syms, **kw: calls.append("cninfo") or iter((cninfo_map or {}).items()))
        return calls
    return _install


def test_auto_uses_eastmoney_when_reachable(patch_sources):
    calls = patch_sources(em_reachable=True, em_map={"600000.SH": "银行Ⅱ"})
    m, src = fetch_a_share_industry_map(["600000.SH"])
    assert (m, src) == ({"600000.SH": "银行Ⅱ"}, "eastmoney")
    assert calls == ["probe", "eastmoney"], "探通了就不该碰巨潮"


def test_auto_falls_back_to_cninfo_when_eastmoney_blocked(patch_sources):
    """东财 IP 被封 → 立刻换巨潮，而不是把 max_rounds 烧完。"""
    calls = patch_sources(em_reachable=False, cninfo_map={"600000.SH": "货币金融服务"})
    m, src = fetch_a_share_industry_map(["600000.SH"])
    assert (m, src) == ({"600000.SH": "货币金融服务"}, "cninfo")
    assert calls == ["probe", "cninfo"], "探不通就不该再翻东财的页"


def test_probe_bounds_the_ip_spend(monkeypatch):
    """探测最多换 2 个 IP —— 别在东财全面封禁时先烧 32 个才知道换源。"""
    import acquisition.markets.industry as ind

    seen = {}

    class _Crawler:
        def __init__(self, **kw):
            seen.update(kw)

        def get_json(self, url, *, params=None, headers=None):
            raise ProxyRetriesExhaustedError("全挂")

    monkeypatch.setattr(ind, "BaseCrawler", _Crawler)
    assert ind._eastmoney_reachable() is False
    assert seen["max_rounds"] == 2, f"探测的换 IP 预算是 {seen['max_rounds']}，太贵了"


def test_one_source_per_run_no_taxonomy_mixing(patch_sources):
    """两套分类口径混进同一列，HHI 就是垃圾。auto 必须只用一个源。"""
    calls = patch_sources(em_reachable=True, em_map={"600000.SH": "银行Ⅱ"},
                          cninfo_map={"600519.SH": "酒、饮料和精制茶制造业"})
    m, _ = fetch_a_share_industry_map(["600000.SH", "600519.SH"])
    assert m == {"600000.SH": "银行Ⅱ"}, "东财通了就不该再混入巨潮的口径"
    assert "cninfo" not in calls


def test_explicit_source_skips_the_probe(patch_sources):
    calls = patch_sources(em_reachable=True, cninfo_map={"600000.SH": "货币金融服务"})
    m, src = fetch_a_share_industry_map(["600000.SH"], source="cninfo")
    assert src == "cninfo" and calls == ["cninfo"], "指名了源就不该探东财"


def test_cninfo_requires_symbols():
    with pytest.raises(ValueError, match="symbols"):
        fetch_a_share_industry_map(None, source="cninfo")


def test_cninfo_never_uses_the_proxy_pool(monkeypatch):
    """巨潮逐只查：空 DataFrame 是**真实答案**（退市票），不是失败。

    挂上代理池的话 `domestic_akshare` 会把空结果当失败去换 IP，
    为每只退市票白烧 max_rounds 个 IP。
    """
    import acquisition.markets.industry as ind

    kwargs_seen = []

    class _Row(dict):
        pass

    class _DF:
        empty = False

        def __init__(self, v):
            self.iloc = [_Row({"所属行业": v})]

    def _fake_ak(fn, **kw):
        kwargs_seen.append(kw)
        return _DF("货币金融服务")

    monkeypatch.setattr("net.domestic.domestic_akshare", _fake_ak)
    monkeypatch.setattr(ind, "_CNINFO_INTERVAL", 0)
    out = dict(ind._iter_cninfo(["600000.SH"]))
    assert out == {"600000.SH": "货币金融服务"}
    assert kwargs_seen[0]["use_proxy_pool"] is False, "巨潮走了代理池 —— 退市票会白烧 IP"
    assert kwargs_seen[0]["symbol"] == "600000", "巨潮要裸码"


def test_cninfo_empty_result_is_not_a_failure(monkeypatch):
    import acquisition.markets.industry as ind

    class _Empty:
        empty = True

    monkeypatch.setattr("net.domestic.domestic_akshare", lambda fn, **kw: _Empty())
    monkeypatch.setattr(ind, "_CNINFO_INTERVAL", 0)
    assert dict(ind._iter_cninfo(["600000.SH", "600001.SH"])) == {}


# ── 风控读库，零出网 ─────────────────────────────────────────────────────────

def _risk_analyzer_tree():
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "portfolio" / "risk_analyzer.py"
    return ast.parse(src.read_text(encoding="utf-8"))


def test_risk_analyzer_never_touches_the_dead_endpoint():
    """`stock_individual_info_em` 打的 qt/stock/get 重度 IP 门控，每调一次白烧 IP。

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
