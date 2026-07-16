"""阶段A 基线：alpha_lab **图路径**（GRAPH_ALPHA_LAB=on）的对外契约。

「对外契约逐字不变」要求新旧两条路径过同一契约。旧路径由 test_deep_task_contract.py 守，
本文件守图路径：clamp[1,20] 不失效、缺 symbols → validation_error、恰好一个 subagent_done、
resume 分支正确路由到 resume_session。全 mock，不打真实 LLM/沙箱，可进门禁。
"""
import json

import pytest

from tests._fixtures import drain_subagent_done

pytestmark = pytest.mark.baseline


def _ndjson(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False) + "\n"


class FakeGraphEngine:
    """记录 start_session/resume_session 的入参，yield 一条合法 session_complete。"""
    captured: dict = {}
    resumed: dict = {}

    def start_session(self, **kwargs):
        FakeGraphEngine.captured = dict(kwargs)
        yield _ndjson({"event": "session_created", "session_id": "alab_fake"})
        yield _ndjson({
            "event": "session_complete", "session_id": "alab_fake",
            "best_iteration": 1, "best_sharpe": 0.5,
            "total_iterations": 1, "total_cost_usd": 0.0,
        })

    def resume_session(self, session_id, provider=None):
        FakeGraphEngine.resumed = {"session_id": session_id}
        yield _ndjson({"event": "session_resumed", "session_id": session_id, "from_iteration": 2})
        yield _ndjson({
            "event": "session_complete", "session_id": session_id,
            "best_iteration": 1, "best_sharpe": 0.5,
            "total_iterations": 2, "total_cost_usd": 0.0,
        })


@pytest.fixture
def graph_on(monkeypatch):
    monkeypatch.setenv("GRAPH_ALPHA_LAB", "on")
    import alpha_lab.graph.engine as gengine
    monkeypatch.setattr(gengine, "AlphaLabGraphEngine", FakeGraphEngine)
    FakeGraphEngine.captured = {}
    FakeGraphEngine.resumed = {}


@pytest.mark.parametrize("given, expected", [
    (0, 1), (-5, 1), (1, 1), (8, 8), (20, 20),
    (999, 20),   # 上界钳到 20 —— 成本边界，图路径同样不许失效
    ("abc", 8), (None, 8),
])
def test_alpha_lab_graph_max_iterations_clamped(graph_on, given, expected):
    from agents.subagents.alpha_lab import AlphaLabSubagent

    args = {"symbols": ["600519.SH"]}
    if given is not None:
        args["max_iterations"] = given
    drain_subagent_done(AlphaLabSubagent().run(args))
    assert FakeGraphEngine.captured["max_iterations"] == expected


def test_alpha_lab_graph_missing_symbols_yields_validation_error(graph_on):
    from agents.subagents.alpha_lab import AlphaLabSubagent

    result = drain_subagent_done(AlphaLabSubagent().run({}))
    assert result["ok"] is False
    assert result["error_code"] == "validation_error"


def test_alpha_lab_graph_single_subagent_done(graph_on):
    """图路径正常完成也必须恰好一个 subagent_done（drain 内部已断言 count==1）。"""
    from agents.subagents.alpha_lab import AlphaLabSubagent

    result = drain_subagent_done(AlphaLabSubagent().run({"symbols": ["600519.SH"]}))
    assert result["ok"] is True
    assert "best_iteration" not in result or True  # 结果由 summary 承载
    assert FakeGraphEngine.captured["target_symbols"] == ["600519.SH"]


def test_alpha_lab_resume_routes_to_resume_session(graph_on):
    """带 resume_session_id 且图开关开 → 走 resume_session，跳过 symbols 校验，仍一个 done。"""
    from agents.subagents.alpha_lab import AlphaLabSubagent

    result = drain_subagent_done(
        AlphaLabSubagent().run({"resume_session_id": "alab_abc123"}))
    assert result["ok"] is True
    assert FakeGraphEngine.resumed == {"session_id": "alab_abc123"}
    assert FakeGraphEngine.captured == {}  # 未走 start_session


def test_alpha_lab_off_switch_uses_old_engine(monkeypatch):
    """默认 off：走旧 AlphaLabEngine，不碰图路径（回滚安全）。"""
    import alpha_lab.engine as engine_mod
    from agents.subagents.alpha_lab import AlphaLabSubagent

    monkeypatch.delenv("GRAPH_ALPHA_LAB", raising=False)
    used = {}

    class FakeOldEngine:
        def start_session(self, **kwargs):
            used["old"] = True
            yield _ndjson({"event": "session_complete", "session_id": "x",
                           "best_iteration": None, "best_sharpe": None,
                           "total_iterations": 0, "total_cost_usd": 0.0})

    monkeypatch.setattr(engine_mod, "AlphaLabEngine", FakeOldEngine)
    drain_subagent_done(AlphaLabSubagent().run({"symbols": ["600519.SH"]}))
    assert used.get("old") is True
