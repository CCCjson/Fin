"""
AlphaLabGraphEngine —— alpha_lab 图路径引擎（阶段A）。

与旧 `AlphaLabEngine` **平级、不继承、不改旧类**。对外 yield 与旧引擎逐字兼容的 NDJSON
（session_created / preparing_data / iteration_* / session_complete …），故 subagent 层
适配逻辑几乎不用改（见 agents/subagents/alpha_lab.py）。

薄用四原语：本文件接 `StateGraph`（build_graph）+ sqlite `checkpointer`（SqliteSaver）。
断点续跑靠 checkpointer 在节点边界落盘 → resume_session 用同 thread_id 续跑（阶段A 的真实新能力）。

回滚：整个 graph/ 子包由 subagent 层 `GRAPH_ALPHA_LAB=off`（默认）回落旧引擎；删除本目录后
旧引擎仍完整（共享数据准备在 alpha_lab.data_prep）。
"""
import os
import sqlite3
import uuid
from collections.abc import Generator
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from loguru import logger

from alpha_lab.code_generator import CodeGenerator
from alpha_lab.data_prep import cleanup_temp_data
from alpha_lab.evaluator import Evaluator
from alpha_lab.graph.build import build_graph
from alpha_lab.graph.nodes import AlphaLabNodes
from alpha_lab.sandbox import Sandbox, _ndjson
from alpha_lab.strategy_store import StrategyStore

# checkpointer 库文件：与 market.db 同目录但独立（回滚删除干净）；env 可覆写
_DEFAULT_GRAPH_DB = Path(__file__).resolve().parents[2] / "data" / "alpha_lab_graph.db"

# max_iterations 硬上限（与旧引擎同源，兜住直接调用图引擎的场景；subagent 已在 fork 前钳制）
MAX_ITERATIONS_CAP = 20


class AlphaLabGraphEngine:
    """Alpha Lab 图引擎（循环子图 + sqlite checkpointer 断点续跑）。"""

    def __init__(self) -> None:
        self.sandbox = Sandbox()
        self.evaluator = Evaluator()
        self.strategy_store = StrategyStore()
        self.code_generator: CodeGenerator | None = None  # 按 provider 每次请求重建

    def _checkpointer(self) -> SqliteSaver:
        db_path = os.getenv("ALPHA_LAB_GRAPH_DB", str(_DEFAULT_GRAPH_DB))
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()  # 幂等建表
        return saver

    def _build(self, provider: str | None) -> object:
        """按 provider 重建 code_generator 并装配带 checkpointer 的图。"""
        # 复位/切换 provider：与旧引擎同语义，避免上次 provider 残留串到本次（误走付费后端）
        self.code_generator = CodeGenerator(provider=provider) if provider else CodeGenerator()
        if provider:
            logger.info(f"Alpha Lab(graph) 切换 provider: {provider}")
        nodes = AlphaLabNodes(
            self.code_generator, self.sandbox, self.evaluator, self.strategy_store)
        return build_graph(nodes, checkpointer=self._checkpointer())

    def start_session(
        self,
        target_symbols: list[str],
        optimization_goal: str,
        data_start: str,
        data_end: str,
        max_iterations: int = 15,
        initial_capital: float = 1000000.0,
        constraints: dict | None = None,
        provider: str | None = None,
    ) -> Generator[str, None, None]:
        """启动新会话，走图路径（同步生成器，yield NDJSON 行）。"""
        # max_iterations 兜底钳制（唯一汇聚点思想；subagent 已钳，这里对直接调用再兜一层）
        try:
            max_iterations = int(max_iterations)
        except (TypeError, ValueError):
            max_iterations = 8
        max_iterations = max(1, min(max_iterations, MAX_ITERATIONS_CAP))

        session_id = f"alab_{uuid.uuid4().hex[:12]}"
        graph = self._build(provider)

        # session_created + 持久化会话行（循环外一次性，不进子图 state）
        yield _ndjson({"event": "session_created", "session_id": session_id})
        self.strategy_store.save_session(
            session_id=session_id,
            target_symbols=target_symbols,
            optimization_goal=optimization_goal,
            data_start=data_start,
            data_end=data_end,
            max_iterations=max_iterations,
            initial_capital=initial_capital,
            constraints=constraints,
        )

        initial_state = {
            "session_id": session_id,
            "target_symbols": target_symbols,
            "optimization_goal": optimization_goal,
            "data_start": data_start,
            "data_end": data_end,
            "max_iterations": max_iterations,
            "initial_capital": initial_capital,
            "constraints": constraints or {},
            "provider": provider,
            "current_iteration": 1,
            "phase": "explore",
            "no_improve_count": 0,
            "fail_streak": 0,
            "messages": [],
            "iterations": [],
            "best_iteration": None,
            "best_val_sharpe": -999.0,
            "best_composite_score": -999.0,
            "total_tokens": 0,
            "total_cost": 0.0,
            "status": "running",
        }
        config = {"configurable": {"thread_id": session_id}}
        yield from self._drive(graph, initial_state, config)

    def resume_session(
        self,
        session_id: str,
        provider: str | None = None,
    ) -> Generator[str, None, None]:
        """从 checkpoint 续跑一个中断的会话（阶段A 的真实新能力）。

        - 无 checkpoint → yield error 事件（subagent 归类为失败）。
        - 已 completed → 直接复现 session_complete 快照，不重跑。
        - 临时 CSV 目录已被清理（进程重启后必然）→ 用 state 里的 symbols+日期区间
          确定性重建，写回 state 后再续跑。
        """
        graph = self._build(provider)
        config = {"configurable": {"thread_id": session_id}}
        snap = graph.get_state(config)
        state = snap.values

        if not state:
            yield _ndjson({"event": "error", "message": f"无可恢复的会话: {session_id}"})
            return

        # 已完成：复现 session_complete，不重跑（避免对 completed thread 空转）
        if state.get("status") == "completed":
            best_sharpe = state.get("best_val_sharpe", -999.0)
            yield _ndjson({
                "event": "session_complete",
                "session_id": session_id,
                "best_iteration": state.get("best_iteration"),
                "best_sharpe": round(best_sharpe, 3) if best_sharpe > -999 else None,
                "total_iterations": state.get("current_iteration", 1) - 1,
                "total_cost_usd": round(state.get("total_cost", 0.0), 4),
            })
            return

        yield _ndjson({
            "event": "session_resumed",
            "session_id": session_id,
            "from_iteration": state.get("current_iteration"),
        })

        # 临时数据目录重建（磁盘路径不可信：resume 常发生在进程重启后）
        self._ensure_data(graph, config, state)

        yield from self._drive(graph, None, config)

    def _ensure_data(self, graph, config, state: dict) -> None:
        """若 train_paths 指向的文件不存在，用确定性重建补回，并写回 checkpoint state。"""
        train_paths = state.get("train_paths") or {}
        first = next(iter(train_paths.values()), None)
        if first and os.path.exists(first):
            return  # 同进程内中断，文件还在，直接复用
        symbols = state.get("target_symbols") or []
        if not symbols:
            return
        from alpha_lab.data_prep import prepare_data
        new_train, new_val, _summary = prepare_data(
            symbols, state["data_start"], state["data_end"])
        tmpdir = os.path.dirname(next(iter(new_train.values())))
        graph.update_state(config, {
            "train_paths": new_train, "val_paths": new_val, "tmpdir": tmpdir,
        })
        logger.info(f"[{state.get('session_id')}] resume 重建数据目录: {tmpdir}")

    def _drive(self, graph, state_or_none, config) -> Generator[str, None, None]:
        """驱动图 stream，逐条转 NDJSON；finally 清理临时目录（覆盖异常逃逸）。"""
        latest: dict = {}
        try:
            # 同时取 custom（事件）与 values（状态快照，供 finally 拿 tmpdir 清理）
            for mode, chunk in graph.stream(
                state_or_none, config=config, stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    yield _ndjson(chunk)
                elif mode == "values":
                    latest = chunk
        finally:
            tmpdir = latest.get("tmpdir")
            if tmpdir and os.path.isdir(tmpdir):
                cleanup_temp_data(
                    latest.get("train_paths") or {}, latest.get("val_paths") or {})
