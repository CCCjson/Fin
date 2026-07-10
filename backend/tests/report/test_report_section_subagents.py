"""13.2-4 行为基线：五个报告章节 subagent 的对外契约。

契约与旧 run_research_report 同源（ToolEnvelope 形状、恰好一个 subagent_done、
tokens 透传），但收尾政策相反：旧的禁止主 agent 复述，新的**要求**它基于各章
结论撰写纵览与操作计划——因为 Ch1 没有对应工具了。

全 mock，不打 LLM、不碰 DB。
"""
import json
import threading

import pytest

from agents.subagents import get_runner
from agents.subagents.base import SUMMARY_NO_RESTATE, SUMMARY_SYNTHESIZE
from agents.subagents.report_sections import SECTION_SUBAGENTS
from tests._fixtures import drain_subagent_done

pytestmark = pytest.mark.baseline

SECTION_NAMES = ["report_market", "report_news", "report_positions",
                 "report_strategy", "report_picks"]


@pytest.fixture(autouse=True)
def _never_touch_the_real_decision_log(monkeypatch):
    """report_picks 的 _after_collect 会调 record_picks 真写库。

    这条曾经**真的把 6 行假推荐写进了生产 market.db**（`000001.SZ`，价格全 None）——
    因为 `_after_collect` 是延迟 import，只 mock 采集器和成稿器拦不住它。
    在 fixture 层无条件挡死，别指望每个用例记得自己 mock。
    """
    monkeypatch.setattr("report_engine.picks_log.record_picks",
                        lambda recs, **kw: (_ for _ in ()).throw(
                            AssertionError("测试不许写 DecisionLog；要断言留痕请用 fake_engine['recorded']")))


@pytest.fixture
def fake_engine(monkeypatch):
    """替换掉 subagent.run 里那几个懒 import 的引擎入口。"""
    state = {"collected": [], "section_args": [], "cancel_event": None,
             "recorded": [], "data": {"portfolio": {"positions": []}}}

    def _fake_record_picks(recs, **kw):
        state["recorded"].append(recs)
        return len(recs)

    monkeypatch.setattr("report_engine.picks_log.record_picks", _fake_record_picks)

    class _FakeCollector:
        def __getattr__(self, item):
            if not item.startswith("collect_"):
                raise AttributeError(item)

            def _collect(report_type="weekly"):
                if state.get("collect_raises"):
                    raise RuntimeError("DB 挂了")
                state["collected"].append((item, report_type))
                return state["data"]
            return _collect

    def _fake_write_section(section, data, *, report_type, model, cancel_event=None):
        state["section_args"].append({"section": section, "report_type": report_type,
                                      "model": model, "data": data})
        state["cancel_event"] = cancel_event
        yield from state.get("events", [
            json.dumps({"event": "collecting", "message": "正在生成…"}) + "\n",
            json.dumps({"event": "chunk", "content": "章节正文"}) + "\n",
            json.dumps({"event": "done", "token_count": 4321}) + "\n",
        ])

    monkeypatch.setattr("report_engine.data_collector.ReportDataCollector", _FakeCollector)
    monkeypatch.setattr("report_engine.section_writer.write_section", _fake_write_section)
    monkeypatch.setattr("llm_config.get_cheap_model", lambda: "fake-cheap")
    return state


# ── 注册与命名 ────────────────────────────────────────────────────────────

def test_all_five_sections_registered_with_stable_names():
    assert sorted(cls.name for cls in SECTION_SUBAGENTS) == sorted(SECTION_NAMES)
    for name in SECTION_NAMES:
        assert get_runner(name).name == name


def test_section_maps_to_collector_method(fake_engine):
    for name in SECTION_NAMES:
        fake_engine["collected"].clear()
        list(get_runner(name).run({}))
        assert fake_engine["collected"] == [(f"collect_{name.removeprefix('report_')}", "weekly")]


# ── envelope 契约 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", SECTION_NAMES)
def test_happy_path_envelope_shape(name, fake_engine):
    env = drain_subagent_done(get_runner(name).run({}))
    assert env["ok"] is True
    assert env.get("error_code") is None
    assert env["business_result"] == "affirmative"
    assert env["tokens"] == 4321                 # 引擎 done.token_count 透传
    assert "widgets" in env and "widget" not in env
    assert env["message"].startswith(SUMMARY_SYNTHESIZE)


@pytest.mark.parametrize("name", SECTION_NAMES)
def test_collect_failure_becomes_internal_error(name, fake_engine):
    fake_engine["collect_raises"] = True
    env = drain_subagent_done(get_runner(name).run({}))
    assert env["ok"] is False
    assert env["error_code"] == "internal_error"
    assert "数据采集失败" in env["message"]


@pytest.mark.parametrize("name", SECTION_NAMES)
def test_empty_output_is_explicit_negative(name, fake_engine):
    fake_engine["events"] = [json.dumps({"event": "done", "token_count": 0}) + "\n"]
    env = drain_subagent_done(get_runner(name).run({}))
    assert env["ok"] is True
    assert env["business_result"] == "negative"


@pytest.mark.parametrize("bad,expected", [("yearly", "weekly"), ("", "weekly"),
                                          ("daily", "daily"), ("monthly", "monthly")])
def test_report_type_normalized(bad, expected, fake_engine):
    list(get_runner("report_market").run({"report_type": bad}))
    assert fake_engine["collected"][-1][1] == expected
    assert fake_engine["section_args"][-1]["report_type"] == expected


# ── cancel 透传（旧 report 路径收了却从不读）───────────────────────────────

@pytest.mark.parametrize("name", SECTION_NAMES)
def test_cancel_event_is_passed_down_to_the_writer(name, fake_engine):
    cancel = threading.Event()
    list(get_runner(name).run({}, cancel_event=cancel))
    assert fake_engine["cancel_event"] is cancel


# ── 收尾政策：MoneyBill 要能写纵览 ─────────────────────────────────────────

def test_summary_prefix_allows_synthesis_unlike_full_report():
    """废掉全量报告后 Ch1 纵览由 MoneyBill 写，摘要前缀语义与旧路径相反。"""
    assert "禁止复述" in SUMMARY_NO_RESTATE
    assert "撰写纵览与操作计划" in SUMMARY_SYNTHESIZE
    assert "禁止逐段复述原文" in SUMMARY_SYNTHESIZE


def test_section_subagents_excluded_from_tail_length_policy():
    """把章节工具加进 _SUBAGENT_NAMES 会把纵览强制压成一句话（150 字上限）。"""
    from agents.policy_checks import _SUBAGENT_NAMES
    assert _SUBAGENT_NAMES.isdisjoint(SECTION_NAMES)


def test_summary_lists_symbols_so_the_overview_is_not_flagged_unbacked(fake_engine):
    """policy_checks 只扫工具结果文本判断代码「有没有依据」；正文只截 600 字进
    context，纵览引用的代码很可能被截掉 → 摘要必须显式带上标的清单。"""
    from agents.policy_checks import _backed_symbols

    fake_engine["data"] = {
        "portfolio": {"positions": [{"symbol": "600519.SH"}]},
        "top_stocks": {"buy_recommendations": [{"symbol": "000001.SZ"}],
                       "sell_warnings": [{"symbol": "300750.SZ"}]},
        "previous_report": {"recommendations": [{"symbol": "601318.SH"}]},
    }
    fake_engine["events"] = [
        json.dumps({"event": "chunk", "content": "正文里一个代码都没写"}) + "\n",
        json.dumps({"event": "done", "token_count": 10}) + "\n",
    ]
    env = drain_subagent_done(get_runner("report_picks").run({}))

    assert "【本章涉及标的】" in env["message"]
    backed = _backed_symbols([{"role": "tool", "content": env["message"]}])
    assert backed == {"600519.SH", "000001.SZ", "300750.SZ", "601318.SH"}


def test_no_symbols_means_no_roster_line(fake_engine):
    env = drain_subagent_done(get_runner("report_market").run({}))
    assert "【本章涉及标的】" not in env["message"]
    assert env["message"].startswith(SUMMARY_SYNTHESIZE)


def test_summary_is_truncated_to_600_chars(fake_engine):
    fake_engine["events"] = [
        json.dumps({"event": "chunk", "content": "字" * 2000}) + "\n",
        json.dumps({"event": "done", "token_count": 1}) + "\n",
    ]
    env = drain_subagent_done(get_runner("report_news").run({}))
    assert "…(已截断)" in env["message"]
    assert env["message"].count("字") == 600


# ── report_picks 的留痕钩子 ───────────────────────────────────────────────

def test_report_picks_records_decisions_before_writing(fake_engine):
    """推荐是选出来的不是写出来的：采完就留痕，不等成稿成功。

    这批 DecisionLog 就是下次 report_strategy 里「上期推荐回顾」的数据源。
    """
    recs = [{"symbol": "600519.SH", "price": 1700.0}]
    fake_engine["data"] = {"portfolio": {"positions": []},
                           "top_stocks": {"buy_recommendations": recs}}

    list(get_runner("report_picks").run({}))
    assert fake_engine["recorded"] == [recs]


def test_other_sections_do_not_record_decisions(fake_engine):
    for name in ("report_market", "report_news", "report_positions", "report_strategy"):
        list(get_runner(name).run({}))
    assert fake_engine["recorded"] == []


def test_report_picks_with_no_recommendations_records_nothing(fake_engine):
    fake_engine["data"] = {"portfolio": {"positions": []}}
    list(get_runner("report_picks").run({}))
    assert fake_engine["recorded"] == [[]]
