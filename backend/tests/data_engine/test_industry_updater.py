"""行业回填器：分批落库 / 断点续跑 / 换源清空旧口径。

背景：全市场逐只查巨潮要跑 33 分钟。第一版一次性攒完再写库——中途挂掉半小时
全白费，且过程完全不可观测（`conda run` 还把进度日志缓冲到结束才吐）。

⚠️ 全程用内存库。`get_session` 被结构性打桩，绝不碰生产 `data/market.db`
（历史事故：延迟 import 的写库调用绕过 mock，把假数据写进了生产库）。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import data_engine.industry_updater as iu
from data_engine.storage.models import Base, StockInfo

pytestmark = pytest.mark.baseline

_SEED = [
    ("000001.SZ", "a_share", 1, "stock", None),
    ("000002.SZ", "a_share", 1, "stock", None),
    ("600000.SH", "a_share", 1, "stock", None),
    ("600519.SH", "a_share", 1, "stock", None),
    ("300750.SZ", "a_share", 0, "stock", None),      # 停牌/退市 → 不该进候选
    ("510300.SH", "a_share", 1, "etf", None),        # ETF → 不该进候选
    ("00700.HK", "hk_stock", 1, "stock", None),      # 港股 → 不该进候选
]


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """内存库 + 打桩 get_session；顺便把源标记文件挪到 tmp。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    make_session = sessionmaker(bind=engine)

    s = make_session()
    for sym, mkt, active, stype, ind in _SEED:
        s.add(StockInfo(symbol=sym, name=sym, market=mkt, is_active=active,
                        stock_type=stype, industry=ind))
    s.commit()
    s.close()

    monkeypatch.setattr(iu, "get_session", make_session)
    monkeypatch.setattr(iu, "_SOURCE_MARKER", tmp_path / "industry_source.json")
    return make_session


def _industries(make_session) -> dict:
    s = make_session()
    try:
        return {r.symbol: r.industry for r in s.query(StockInfo).all() if r.industry}
    finally:
        s.close()


def _stub_stream(monkeypatch, source, pairs, *, on_symbols=None):
    """打桩取数层：返回 (source, 迭代器)，并记录喂进来的 symbol 列表。

    ⚠️ 巨潮源**只产出被要求的 symbol**——它是逐只查的。假对象若无视 `symbols`
    照吐全部，`resume` 相关的测试就会假绿（第一版正是如此：它顺手覆盖了脏数据，
    掩盖了「resume 只补缺口、留下旧口径」这个真 bug）。东财源翻全市场页，不筛。
    """
    def _fake_resolve(symbols=None, **kw):
        return source          # 探测结果由打桩决定，无视调用方传的 "auto"

    def _fake_stream(symbols=None, *, source=None, limit=None):
        if on_symbols is not None:
            on_symbols.append(list(symbols or []))
        wanted = set(symbols or [])
        out = pairs if source == "eastmoney" else [p for p in pairs if p[0] in wanted]
        return source, iter(out)

    monkeypatch.setattr(iu, "resolve_source", _fake_resolve)
    monkeypatch.setattr(iu, "stream_a_share_industry", _fake_stream)


# ── 候选集 ──────────────────────────────────────────────────────────────────

def test_only_active_non_etf_a_shares_are_candidates(db, monkeypatch):
    seen: list[list[str]] = []
    _stub_stream(monkeypatch, "cninfo", [], on_symbols=seen)
    iu.backfill_industry()
    assert seen[0] == ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH"], \
        "停牌股 / ETF / 港股不该进候选"


# ── 分批落库 ────────────────────────────────────────────────────────────────

def test_commits_in_batches_not_all_at_the_end(db, monkeypatch):
    """跑到一半挂掉，前面几批必须已经落库。"""
    pairs = [("000001.SZ", "银行"), ("000002.SZ", "地产"), ("600000.SH", "银行")]

    committed_at: list[dict] = []
    real_commit = iu._commit_batch

    def _spy(batch):
        n = real_commit(batch)
        committed_at.append(dict(batch))
        return n

    monkeypatch.setattr(iu, "_commit_batch", _spy)
    _stub_stream(monkeypatch, "cninfo", pairs)
    iu.backfill_industry(batch_size=2)

    assert [len(b) for b in committed_at] == [2, 1], f"没有分批：{committed_at}"
    assert _industries(db) == {"000001.SZ": "银行", "000002.SZ": "地产", "600000.SH": "银行"}


def test_partial_run_keeps_what_it_wrote(db, monkeypatch):
    """取数中途抛异常 → 已 commit 的批次留在库里（这正是分批的意义）。"""
    def _boom():
        yield ("000001.SZ", "银行")
        yield ("000002.SZ", "地产")
        raise RuntimeError("巨潮挂了")

    monkeypatch.setattr(iu, "resolve_source", lambda symbols=None, *, source=None: "cninfo")
    monkeypatch.setattr(iu, "stream_a_share_industry",
                        lambda symbols=None, *, source=None, limit=None: ("cninfo", _boom()))

    with pytest.raises(RuntimeError, match="巨潮挂了"):
        iu.backfill_industry(batch_size=2)
    assert _industries(db) == {"000001.SZ": "银行", "000002.SZ": "地产"}, "前一批该已落库"


# ── 断点续跑 ────────────────────────────────────────────────────────────────

def test_resume_only_fetches_missing(db, monkeypatch):
    _stub_stream(monkeypatch, "cninfo", [("000001.SZ", "银行"), ("000002.SZ", "地产")])
    iu.backfill_industry()

    seen: list[list[str]] = []
    _stub_stream(monkeypatch, "cninfo", [("600000.SH", "银行")], on_symbols=seen)
    out = iu.backfill_industry(resume=True)

    assert seen[0] == ["600000.SH", "600519.SH"], "resume 该只喂缺行业的票"
    assert out["skipped_resume"] == 2


def test_resume_with_different_source_falls_back_to_full_run(db, monkeypatch):
    """换了源还 resume，就会把两套分类口径混进同一列 —— HHI 直接报废。"""
    _stub_stream(monkeypatch, "cninfo", [("000001.SZ", "货币金融服务")])
    iu.backfill_industry()
    assert _industries(db) == {"000001.SZ": "货币金融服务"}

    seen: list[list[str]] = []
    _stub_stream(monkeypatch, "eastmoney", [("000002.SZ", "房地产开发")], on_symbols=seen)
    out = iu.backfill_industry(resume=True)

    assert out["skipped_resume"] == 0, "换源时 resume 必须降级为全量"
    assert len(seen[0]) == 4, "全量重跑该喂上全部候选"
    assert _industries(db) == {"000002.SZ": "房地产开发"}, "旧口径必须被清空"


def test_switching_source_clears_old_taxonomy(db, monkeypatch):
    _stub_stream(monkeypatch, "cninfo", [("000001.SZ", "货币金融服务"),
                                         ("600519.SH", "酒、饮料和精制茶制造业")])
    iu.backfill_industry()
    assert len(_industries(db)) == 2

    _stub_stream(monkeypatch, "eastmoney", [("000001.SZ", "银行Ⅱ")])
    iu.backfill_industry()
    assert _industries(db) == {"000001.SZ": "银行Ⅱ"}, "巨潮口径的茅台行业没被清掉"


def test_same_source_rerun_does_not_clear(db, monkeypatch):
    _stub_stream(monkeypatch, "cninfo", [("000001.SZ", "货币金融服务")])
    iu.backfill_industry()
    _stub_stream(monkeypatch, "cninfo", [("000002.SZ", "房地产业")])
    iu.backfill_industry()
    assert _industries(db) == {"000001.SZ": "货币金融服务", "000002.SZ": "房地产业"}


def test_resume_with_nothing_missing_is_a_noop(db, monkeypatch):
    _stub_stream(monkeypatch, "cninfo", [(s, "X") for s in
                                         ("000001.SZ", "000002.SZ", "600000.SH", "600519.SH")])
    iu.backfill_industry()

    called: list[list[str]] = []
    _stub_stream(monkeypatch, "cninfo", [], on_symbols=called)
    out = iu.backfill_industry(resume=True)
    assert out["fetched"] == 0 and called == [], "没缺的就别再出网"


# ── 源标记 ──────────────────────────────────────────────────────────────────

def test_source_marker_records_the_run(db, monkeypatch):
    _stub_stream(monkeypatch, "cninfo", [("000001.SZ", "货币金融服务")])
    iu.backfill_industry()
    assert iu._read_last_source() == "cninfo"


def test_missing_marker_means_first_run(db, monkeypatch):
    assert iu._read_last_source() is None
    _stub_stream(monkeypatch, "eastmoney", [("000001.SZ", "银行Ⅱ")])
    iu.backfill_industry()          # 不该因为没有 marker 就去清空
    assert _industries(db) == {"000001.SZ": "银行Ⅱ"}


def test_marker_is_written_before_the_first_batch_lands(db, monkeypatch):
    """崩溃的半截任务也必须留下源标记 —— 否则下一轮认不出该清空。

    这个洞是「分批 commit」自己挖的：以前跑挂了库里啥也没有，混不了口径；
    现在半途的批次留在库里，marker 却没写。
    """
    def _crash():
        yield ("000001.SZ", "银行Ⅱ")
        raise RuntimeError("东财挂了")

    monkeypatch.setattr(iu, "resolve_source", lambda symbols=None, **kw: "eastmoney")
    monkeypatch.setattr(iu, "stream_a_share_industry",
                        lambda symbols=None, *, source=None, limit=None: ("eastmoney", _crash()))

    with pytest.raises(RuntimeError):
        iu.backfill_industry(batch_size=1)

    assert _industries(db) == {"000001.SZ": "银行Ⅱ"}, "第一批该已落库"
    assert iu._read_last_source() == "eastmoney", \
        "崩溃的半截任务没留下源标记 —— 下一轮换源时不会清空，两套口径会混在一列"


def test_resume_after_a_crashed_run_with_another_source_clears_first(db, monkeypatch):
    """崩溃的东财半截 + 巨潮 resume ⇒ 必须先清空，绝不能只补缺口。"""
    def _crash():
        yield ("000001.SZ", "银行Ⅱ")
        raise RuntimeError("东财挂了")

    monkeypatch.setattr(iu, "resolve_source", lambda symbols=None, **kw: "eastmoney")
    monkeypatch.setattr(iu, "stream_a_share_industry",
                        lambda symbols=None, *, source=None, limit=None: ("eastmoney", _crash()))
    with pytest.raises(RuntimeError):
        iu.backfill_industry(batch_size=1)

    # 换巨潮 + resume：库里那条申万口径的「银行Ⅱ」必须先被清掉
    _stub_stream(monkeypatch, "cninfo",
                 [(s, "货币金融服务") for s in ("000001.SZ", "000002.SZ",
                                            "600000.SH", "600519.SH")])
    iu.backfill_industry(resume=True)

    assert set(_industries(db).values()) == {"货币金融服务"}, \
        f"两套口径混在了一列：{_industries(db)}"
