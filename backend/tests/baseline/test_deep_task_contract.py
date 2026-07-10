"""13.1 基线：深任务（deep_stock / alpha_lab / news）的对外契约。

13.3 会把它们搬到 LangGraph 子图上。搬完之后，**对外契约必须逐字不变**：
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


# ─────────────────────── 三者共同的 wire 契约 ───────────────────────

def test_all_subagents_declare_stable_names():
    """subagent 的 name 就是模型看到的工具名，迁 LangGraph 后不许改。

    report 已于 13.2 拆成五个章节 subagent（run_research_report 退役），
    它们的契约在 tests/report/test_report_section_subagents.py 里守着。
    """
    from agents.subagents.alpha_lab import AlphaLabSubagent
    from agents.subagents.deep_stock import DeepStockSubagent
    from agents.subagents.news import NewsSubagent

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
    for name in ("report_engine.section_writer", "alpha_lab.engine"):
        mod = sys.modules.get(name)
        assert mod is None or isinstance(mod, types.ModuleType)
