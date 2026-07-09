"""13.1 基线：三个深任务（report / deep_stock / alpha_lab）的对外契约。

13.2/13.3 会把这三个搬到 LangGraph 子图上。搬完之后，**对外契约必须逐字不变**：
- 恰好一个 subagent_done 收尾
- ToolEnvelope 的 ok / error_code / business_result 形状不变
- deep_stock 的只读工具白名单不许放宽（安全边界）
- alpha_lab 的迭代次数钳位不许失效（成本边界）

这里全部 mock，不打真实 LLM，可进门禁。内容质量的对比靠 C7 落盘的真实样本人工 diff。
"""
import sys
import types

import pytest

from tests._fixtures import drain_subagent_done

pytestmark = pytest.mark.baseline


# ─────────────────────────── deep_stock ───────────────────────────

def test_deep_stock_readonly_tool_whitelist_unchanged():
    """deep_stock 在隔离 session 里跑 mini-ReAct，只许碰这 6 个只读工具。

    放宽这个集合 = 让子代理能下单/改配置。迁 LangGraph 时若把工具集换成
    「主循环全量工具」，这条会红。
    """
    from agents.subagents.deep_stock import _DEEP_STOCK_TOOLS

    assert _DEEP_STOCK_TOOLS == {
        "get_daily_data",
        "get_realtime_quote",
        "get_cockpit_score",
        "get_news_sentiment",
        "get_stock_signals",
        "search_knowledge",
    }


def test_deep_stock_whitelist_contains_no_confirmation_tools():
    """白名单里不许出现任何需要二次确认的工具（下单、改风控…）。"""
    import agents  # noqa: F401  触发工具注册副作用
    from agents.registry import REGISTRY
    from agents.subagents.deep_stock import _DEEP_STOCK_TOOLS

    for name in _DEEP_STOCK_TOOLS:
        tool = REGISTRY.get(name)
        assert tool is not None, f"白名单里的 {name} 没注册"
        assert not getattr(tool, "requires_confirmation", False), (
            f"{name} 需要二次确认，不该出现在 deep_stock 的只读白名单里"
        )


def test_deep_stock_missing_symbol_yields_validation_error():
    from agents.subagents.deep_stock import DeepStockSubagent

    result = drain_subagent_done(DeepStockSubagent().run({}))
    assert result["ok"] is False
    assert result["error_code"] == "validation_error"


# ─────────────────────────── alpha_lab ───────────────────────────

@pytest.mark.parametrize("given, expected", [
    (0, 1),          # 下界钳到 1
    (-5, 1),
    (1, 1),
    (8, 8),
    (20, 20),
    (999, 20),       # 上界钳到 20 —— 成本边界，不许失效
    ("abc", 8),      # 非法值退默认
    (None, 8),
])
def test_alpha_lab_max_iterations_clamped(monkeypatch, given, expected):
    """迭代次数直接决定 LLM 花销。钳位失效 = 一次会话烧掉几十轮。"""
    import alpha_lab.engine as engine_mod
    from agents.subagents.alpha_lab import AlphaLabSubagent

    captured = {}

    class FakeEngine:
        def start_session(self, **kwargs):
            captured.update(kwargs)
            yield {"type": "session_complete", "best_strategy": None, "iterations": []}

    monkeypatch.setattr(engine_mod, "AlphaLabEngine", FakeEngine)

    args = {"symbols": ["600519.SH"]}
    if given is not None:
        args["max_iterations"] = given

    drain_subagent_done(AlphaLabSubagent().run(args))
    assert captured["max_iterations"] == expected


def test_alpha_lab_missing_symbols_yields_validation_error():
    from agents.subagents.alpha_lab import AlphaLabSubagent

    result = drain_subagent_done(AlphaLabSubagent().run({}))
    assert result["ok"] is False
    assert result["error_code"] == "validation_error"


# ─────────────────────────── report ───────────────────────────

def _fake_report_engines(monkeypatch, chunks: list[str], *, raise_on_collect=False):
    """把 report 的三层引擎全换成桩。注意打的是**源模块符号**——
    report.py 在函数体内延迟 import，patch agents.subagents.report 上的引用无效。
    """
    import report_engine.data_collector as dc_mod
    import report_engine.generator as gen_mod
    import report_engine.planner as planner_mod
    import report_engine.prompt_builder as pb_mod

    class FakeCollector:
        def collect(self, report_type="weekly"):
            if raise_on_collect:
                raise RuntimeError("数据收集炸了")
            return {"period_start": "2026-06-29", "period_end": "2026-07-03"}

    class FakePlan:
        specs = []
        deps = {}

    class FakePlanner:
        def plan(self, data, report_type="weekly"):
            return FakePlan()

    class FakeGenerator:
        def generate_multi_stream(self, **kwargs):
            import json as _json
            # 注意：引擎侧 NDJSON 用的键是 "event"（不是 "type"），token 键是 "token_count"
            for c in chunks:
                yield _json.dumps({"event": "chunk", "content": c}, ensure_ascii=False) + "\n"
            yield _json.dumps({"event": "done", "token_count": 150}, ensure_ascii=False) + "\n"

    monkeypatch.setattr(dc_mod, "ReportDataCollector", FakeCollector)
    monkeypatch.setattr(planner_mod, "ReportPlanner", FakePlanner)
    monkeypatch.setattr(gen_mod, "ReportGenerator", FakeGenerator)
    monkeypatch.setattr(pb_mod, "ReportPromptBuilder", lambda: types.SimpleNamespace())


def test_report_happy_path_envelope_shape(monkeypatch):
    from agents.subagents.report import ReportSubagent

    _fake_report_engines(monkeypatch, ["## 第一章\n", "正文内容。\n"])
    result = drain_subagent_done(ReportSubagent().run({"report_type": "weekly"}))

    assert result["ok"] is True
    assert result.get("error_code") is None
    assert result["business_result"] == "affirmative"
    assert isinstance(result.get("widgets", []), list), "wire 上只许有复数 widgets"
    assert "widget" not in result, "单数 widget 不该出现在 wire 上"
    assert result["message"], "有正文时 message 不该为空"
    assert result["tokens"] == 150, "引擎 done 事件里的 token_count 要透传到 envelope"
    assert "禁止复述" in result["message"], (
        "摘要必须带「禁止复述」前缀——正文已流式展示过，主 agent 再复述会给出重复且矛盾的建议"
    )


def test_report_engine_failure_becomes_internal_error(monkeypatch):
    from agents.subagents.report import ReportSubagent

    _fake_report_engines(monkeypatch, [], raise_on_collect=True)
    result = drain_subagent_done(ReportSubagent().run({"report_type": "weekly"}))

    assert result["ok"] is False
    assert result["error_code"] == "internal_error"


def test_report_empty_output_is_explicit_negative_not_silent_success(monkeypatch):
    """一个字都没生成时必须显式报 negative，不能装作成功。"""
    from agents.subagents.report import ReportSubagent

    _fake_report_engines(monkeypatch, [])
    result = drain_subagent_done(ReportSubagent().run({"report_type": "weekly"}))

    assert result["ok"] is True
    assert result["business_result"] == "negative"


@pytest.mark.parametrize("bad_type, normalized", [
    ("yearly", "weekly"),
    ("", "weekly"),
    ("daily", "daily"),
    ("monthly", "monthly"),
])
def test_report_type_normalized(monkeypatch, bad_type, normalized):
    import report_engine.data_collector as dc_mod
    from agents.subagents.report import ReportSubagent

    seen = {}

    class FakeCollector:
        def collect(self, report_type="weekly"):
            seen["report_type"] = report_type
            return {"period_start": "2026-06-29", "period_end": "2026-07-03"}

    _fake_report_engines(monkeypatch, ["x"])
    monkeypatch.setattr(dc_mod, "ReportDataCollector", FakeCollector)

    drain_subagent_done(ReportSubagent().run({"report_type": bad_type}))
    assert seen["report_type"] == normalized


# ─────────────────────── 三者共同的 wire 契约 ───────────────────────

def test_all_subagents_declare_stable_names():
    """subagent 的 name 就是模型看到的工具名，迁 LangGraph 后不许改。"""
    from agents.subagents.alpha_lab import AlphaLabSubagent
    from agents.subagents.deep_stock import DeepStockSubagent
    from agents.subagents.news import NewsSubagent
    from agents.subagents.report import ReportSubagent

    assert ReportSubagent.name == "run_research_report"
    assert DeepStockSubagent.name == "run_deep_stock"
    assert AlphaLabSubagent.name == "run_alpha_lab"
    assert NewsSubagent.name == "run_news_analysis"


def test_subagent_done_is_the_only_terminal_event():
    """drain_subagent_done 内部已断言「恰好一条」。这里再显式确认 news 也守约。"""
    import news_engine.realtime as realtime_mod
    from agents.subagents.news import NewsSubagent

    original = realtime_mod.get_realtime_sentiment
    try:
        realtime_mod.get_realtime_sentiment = lambda *a, **kw: {"available": False}
        result = drain_subagent_done(NewsSubagent().run({"symbol": "600519.SH"}))
        assert result["ok"] is True
        assert result["business_result"] == "negative"
    finally:
        realtime_mod.get_realtime_sentiment = original


def test_no_stray_sys_modules_pollution():
    """确保上面的 monkeypatch 都还原了，没往 sys.modules 里塞假货。"""
    for name in ("report_engine.generator", "alpha_lab.engine"):
        mod = sys.modules.get(name)
        assert mod is None or isinstance(mod, types.ModuleType)
