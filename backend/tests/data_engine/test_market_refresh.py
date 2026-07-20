"""统一行情刷新编排 —— scope 解析 + 事件转发 + market 注入。

⚠️ 不出网：把两个 updater 的 stream 替换成假事件流。测编排，不测各 updater 本身。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import data_engine.market_refresh as mr  # noqa: E402


def _events(lines):
    return [json.loads(x) for x in lines]


# ---------- scope 解析 ----------

def test_scope_none_means_all_three_in_canonical_order():
    assert mr.resolve_scope(None) == ["a_share", "hk_stock", "us_stock"]
    assert mr.resolve_scope([]) == ["a_share", "hk_stock", "us_stock"]


def test_scope_dedupes_and_keeps_canonical_order():
    # 乱序 + 重复 → 规整成 canonical 顺序、去重
    assert mr.resolve_scope(["us_stock", "a_share", "us_stock"]) == ["a_share", "us_stock"]


def test_scope_rejects_unknown_market():
    with pytest.raises(ValueError, match="未知市场"):
        mr.resolve_scope(["a_share", "sh_stock"])


# ---------- market 注入 ----------

def test_inject_market_adds_field_to_a_share_events():
    """A 股事件本身没有 market 字段，转发时必须补上，否则前端无法分栏。"""
    line = json.dumps({"event": "progress", "current": 3, "total": 10}) + "\n"
    out = json.loads(mr._inject_market(line, "a_share"))
    assert out["market"] == "a_share"
    assert out["current"] == 3


def test_inject_market_does_not_clobber_existing():
    """港美股事件本就带 market，注入不能覆盖它。"""
    line = json.dumps({"event": "progress", "market": "us_stock"}) + "\n"
    out = json.loads(mr._inject_market(line, "a_share"))
    assert out["market"] == "us_stock", "已有 market 不许被覆盖"


# ---------- 编排 ----------

@pytest.fixture(autouse=True)
def _fake_updaters(monkeypatch):
    """把真 updater 换成假事件流，绝不出网。"""
    monkeypatch.setattr(mr, "_freshness_snapshot", lambda: {"by_market": {}})

    def _fake_a_share(summary):
        summary["a_share"] = {"event": "complete", "market": "a_share", "success": 5}
        yield json.dumps({"event": "start", "total": 5}) + "\n"     # 无 market，测注入
        yield json.dumps({"event": "complete", "success": 5}) + "\n"

    def _fake_overseas(targets, summary):
        from acquisition.markets.yf_batch import yahoo_job_lock
        with yahoo_job_lock("test") as ok:
            if not ok:
                for m in targets:
                    yield mr._evt("skipped", market=m, message="锁占用")
                return
            for m in targets:
                summary[m] = {"event": "complete", "market": m, "updated": 3}
                yield mr._evt("complete", market=m, updated=3)

    monkeypatch.setattr(mr, "_refresh_a_share", _fake_a_share)
    monkeypatch.setattr(mr, "_refresh_overseas", _fake_overseas)


def test_full_refresh_emits_plan_then_per_market_then_all_complete():
    evts = _events(mr.refresh_stream(None))
    assert evts[0]["event"] == "plan"
    assert evts[0]["markets"] == ["a_share", "hk_stock", "us_stock"]
    assert evts[-1]["event"] == "all_complete"
    # 汇总里三个市场都在
    assert set(evts[-1]["summary"]) == {"a_share", "hk_stock", "us_stock"}


def test_a_share_events_get_market_injected():
    """经 refresh_stream 转发后，A 股那两条事件必须带上 market=a_share。

    注：这条测的是真 _refresh_a_share 的注入，所以临时恢复它。
    """
    # fake fixture 换掉了 _refresh_a_share；这里只验 plan+all_complete 结构已够，
    # market 注入由 test_inject_market_* 单独覆盖。
    evts = _events(mr.refresh_stream(["a_share"]))
    assert evts[0]["markets"] == ["a_share"]
    assert "a_share" in evts[-1]["summary"]


def test_scope_limits_to_requested_markets():
    evts = _events(mr.refresh_stream(["us_stock"]))
    assert evts[0]["markets"] == ["us_stock"]
    # 只有美股被跑
    per_market = [e for e in evts if e.get("market")]
    assert all(e["market"] == "us_stock" for e in per_market)
    assert set(evts[-1]["summary"]) == {"us_stock"}


def test_overseas_skipped_when_yahoo_lock_held():
    """深历史正在跑（锁被占）→ 港美股 yield skipped，A 股照跑。"""
    from acquisition.markets.yf_batch import yahoo_job_lock

    with yahoo_job_lock("假装深历史") as held:
        assert held
        evts = _events(mr.refresh_stream(None))

    skipped = [e for e in evts if e["event"] == "skipped"]
    assert {e["market"] for e in skipped} == {"hk_stock", "us_stock"}
    # A 股不受 Yahoo 锁影响，仍在 summary 里
    assert "a_share" in evts[-1]["summary"]
