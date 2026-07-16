"""阶段A：alpha_lab 图路径断点续跑冒烟（全 mock，无真实 LLM/沙箱/行情）。

验证核心新能力——iter2 回测崩溃 → checkpoint 落盘 → resume 从节点边界续跑：
不重跑已完成轮次、临时目录被清理后确定性重建、早停计数器随 checkpoint 完整、
最终 iterations 无重复无丢失、恰好一个 subagent_done。
"""
import json

import pytest

from tests._fixtures import drain_subagent_done


class _FakeCodeGen:
    def __init__(self):
        self.gen_calls = 0

    def get_model_for_phase(self, phase):
        return "gpt-5.4-mini" if phase == "explore" else "gpt-5.5"

    def build_initial_messages(self, symbols, optimization_goal, data_summary, constraints):
        return [{"role": "user", "content": "go"}]

    def build_iteration_messages(self, messages, iteration, **kw):
        return messages + [{"role": "user", "content": f"iter {iteration}"}]

    def generate(self, messages, model, temperature):
        self.gen_calls += 1
        return ("def on_bar(h): pass", "reasoning", 100)


class _FaultySandbox:
    """iter2 首次回测抛异常模拟崩溃；disarm 后正常。val_sharpe 随调用递增。"""
    def __init__(self):
        self.n = 0
        self.armed = True

    def ast_check(self, code):
        return True, []

    def execute_backtest(self, strategy_code, symbol, train_data_path, val_data_path, initial_capital):
        self.n += 1
        if self.armed and self.n == 2:
            raise RuntimeError("模拟进程崩溃于 iter2 回测")
        s = 0.5 + self.n * 0.1
        return {
            "success": True,
            "train_metrics": {"sharpe_ratio": s + 0.2, "total_return": 10},
            "val_metrics": {"sharpe_ratio": s, "total_return": 5, "annualized_return": 8},
            "execution_time": 0.01,
        }


class _FakeStore:
    def save_session(self, **kw): pass
    def save_strategy(self, **kw): return "sid"
    def add_log(self, *a, **kw): pass
    def update_session_status(self, *a, **kw): pass


_FAKE_PATHS = (
    {"AAPL": "/tmp/alab_data_resume_gone/AAPL_train.csv"},
    {"AAPL": "/tmp/alab_data_resume_gone/AAPL_val.csv"},
    {"train_days": 100, "val_days": 40},
)


def _make_engine(sandbox, codegen, tmp_db):
    """构造 AlphaLabGraphEngine，注入 fakes，checkpointer 指向 tmp_db。"""
    import os

    from alpha_lab.evaluator import Evaluator
    from alpha_lab.graph.build import build_graph
    from alpha_lab.graph.engine import AlphaLabGraphEngine
    from alpha_lab.graph.nodes import AlphaLabNodes

    os.environ["ALPHA_LAB_GRAPH_DB"] = str(tmp_db)
    eng = AlphaLabGraphEngine()
    eng.strategy_store = _FakeStore()

    def _build(provider):
        eng.code_generator = codegen
        nodes = AlphaLabNodes(codegen, sandbox, Evaluator(), eng.strategy_store)
        return build_graph(nodes, checkpointer=eng._checkpointer())

    eng._build = _build
    return eng


def _read_state(tmp_db, sid, sandbox):
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from alpha_lab.evaluator import Evaluator
    from alpha_lab.graph.build import build_graph
    from alpha_lab.graph.nodes import AlphaLabNodes
    conn = sqlite3.connect(str(tmp_db), check_same_thread=False)
    g = build_graph(
        AlphaLabNodes(_FakeCodeGen(), sandbox, Evaluator(), _FakeStore()),
        checkpointer=SqliteSaver(conn))
    return g.get_state({"configurable": {"thread_id": sid}})


def test_resume_from_crash_does_not_rerun_completed_rounds(tmp_path, monkeypatch):
    tmp_db = tmp_path / "alab_graph.db"
    sandbox = _FaultySandbox()
    codegen = _FakeCodeGen()
    monkeypatch.setattr("alpha_lab.graph.nodes.prepare_data", lambda *a, **k: _FAKE_PATHS)

    # ── 第一段：跑到 iter2 崩溃 ──
    eng = _make_engine(sandbox, codegen, tmp_db)
    sid = None
    with pytest.raises(RuntimeError):
        for line in eng.start_session(
            target_symbols=["AAPL"], optimization_goal="sharpe",
            data_start="2023-01-01", data_end="2025-12-31", max_iterations=3):
            ev = json.loads(line)
            if ev["event"] == "session_created":
                sid = ev["session_id"]

    assert sid is not None
    assert codegen.gen_calls == 2  # iter1 + iter2 生成已跑（崩在 iter2 回测）

    # 中断点：停在 backtest 前，只 commit 了 iter1
    snap = _read_state(tmp_db, sid, sandbox)
    assert snap.values["current_iteration"] == 2
    assert len(snap.values["iterations"]) == 1
    assert snap.next == ("backtest",)  # 节点边界：iter2 的 generate/ast 已落盘，只待 backtest

    # ── 第二段：disarm + resume（临时目录已「被清理」，触发确定性重建）──
    sandbox.armed = False
    gen_before = codegen.gen_calls
    rebuilt = {"n": 0}

    def _counting_prepare(*a, **k):
        rebuilt["n"] += 1
        return _FAKE_PATHS
    monkeypatch.setattr("alpha_lab.data_prep.prepare_data", _counting_prepare)

    eng2 = _make_engine(sandbox, codegen, tmp_db)
    resume_events = [json.loads(x)["event"] for x in eng2.resume_session(sid)]

    # iter2 的 generate 不该重跑（节点边界续跑），只有 iter3 新生成
    assert codegen.gen_calls - gen_before == 1
    # resume 直接从 backtest 续起，不重发 iter2 的 generating_code
    assert resume_events[0] == "session_resumed"
    assert "generating_code" not in resume_events[:3]
    assert resume_events[1] == "backtest_running"
    assert rebuilt["n"] >= 1  # 临时目录被清理 → 确定性重建至少一次
    assert "session_complete" in resume_events

    # 最终状态：无重复无丢失，计数器完整
    final = _read_state(tmp_db, sid, sandbox).values
    assert final["status"] == "completed"
    assert [r["iteration"] for r in final["iterations"]] == [1, 2, 3]
    assert final["best_iteration"] == 3
    assert final["fail_streak"] == 0


def test_resume_unknown_session_yields_error(tmp_path, monkeypatch):
    tmp_db = tmp_path / "alab_graph.db"
    eng = _make_engine(_FaultySandbox(), _FakeCodeGen(), tmp_db)
    events = [json.loads(x) for x in eng.resume_session("alab_does_not_exist")]
    assert len(events) == 1
    assert events[0]["event"] == "error"


def test_resume_completed_session_replays_snapshot(tmp_path, monkeypatch):
    """已完成会话 resume → 复现 session_complete 快照，不重跑（gen 调用不增）。"""
    tmp_db = tmp_path / "alab_graph.db"
    sandbox = _FaultySandbox()
    sandbox.armed = False  # 全程不崩
    codegen = _FakeCodeGen()
    monkeypatch.setattr("alpha_lab.graph.nodes.prepare_data", lambda *a, **k: _FAKE_PATHS)

    eng = _make_engine(sandbox, codegen, tmp_db)
    sid = None
    for line in eng.start_session(
        target_symbols=["AAPL"], optimization_goal="sharpe",
        data_start="2023-01-01", data_end="2025-12-31", max_iterations=2):
        ev = json.loads(line)
        if ev["event"] == "session_created":
            sid = ev["session_id"]

    gen_after_run = codegen.gen_calls
    eng2 = _make_engine(sandbox, codegen, tmp_db)
    events = [json.loads(x) for x in eng2.resume_session(sid)]
    assert len(events) == 1
    assert events[0]["event"] == "session_complete"
    assert codegen.gen_calls == gen_after_run  # 未重跑任何生成


def test_resume_single_subagent_done_via_wrapper(tmp_path, monkeypatch):
    """经 subagent 层（GRAPH_ALPHA_LAB=on + resume_session_id）resume 仍恰好一个 done。"""
    tmp_db = tmp_path / "alab_graph.db"
    sandbox = _FaultySandbox()
    sandbox.armed = False
    codegen = _FakeCodeGen()
    monkeypatch.setattr("alpha_lab.graph.nodes.prepare_data", lambda *a, **k: _FAKE_PATHS)
    monkeypatch.setenv("GRAPH_ALPHA_LAB", "on")

    eng = _make_engine(sandbox, codegen, tmp_db)
    sid = None
    for line in eng.start_session(
        target_symbols=["AAPL"], optimization_goal="sharpe",
        data_start="2023-01-01", data_end="2025-12-31", max_iterations=2):
        ev = json.loads(line)
        if ev["event"] == "session_created":
            sid = ev["session_id"]

    # subagent 层 resume：先构造好复用同一 tmp_db 的引擎实例（必须在 patch 之前，
    # 否则 _make_engine 里的 AlphaLabGraphEngine() 会命中 patch 造成无限递归），
    # 再把类名 patch 成返回这个预构造实例。
    eng2 = _make_engine(sandbox, codegen, tmp_db)
    import alpha_lab.graph.engine as gengine
    monkeypatch.setattr(gengine, "AlphaLabGraphEngine", lambda: eng2)
    from agents.subagents.alpha_lab import AlphaLabSubagent
    result = drain_subagent_done(
        AlphaLabSubagent().run({"resume_session_id": sid}))
    assert result["ok"] is True
