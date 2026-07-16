"""
AlphaLabGraphState —— LangGraph 图路径的持久化状态（阶段A）。

用 TypedDict（LangGraph 原生首选）。蓝本是 `session_manager.SessionState`，但**关键差异**：
旧引擎里 `no_improve_count` / `fail_streak` 是 `start_session` 的局部变量（engine.py:135-136），
`phase` 靠 `session.phase` 承载。图路径必须把这三者连同 best/计数/迭代游标**全部提升进持久化
State**，否则断点续跑（checkpointer 恢复）时早停会误算。

序列化纪律（CODING_STANDARDS §6.4）：全字段必须 JSON 友好——
- `iterations` 存 IterationRecord 的 dict（dataclasses.asdict），不存 dataclass 实例
- `backtest_result` 是 sandbox result.json 解析结果，天然 JSON
- 禁止塞 DataFrame / 文件句柄 / Popen 对象（断点恢复会坏）

Reducer 纪律：本图是**串行单分支**，无并行超步。`messages` / `iterations` 用默认「整值覆写」
语义（节点内读全量→append→写回全量），**不要**用 `add_messages` 之类 reducer——那是并行合并用的，
在这里会导致 resume 后 messages 重复累加、LLM context 翻倍烧钱。
"""
from typing import TypedDict


class AlphaLabGraphState(TypedDict, total=False):
    # ── 不可变输入（create 时定，全程只读）──
    session_id: str                      # == checkpointer thread_id
    target_symbols: list[str]
    symbol: str                          # target_symbols[0]（当前单股）
    optimization_goal: str
    data_start: str
    data_end: str
    max_iterations: int                  # 已在 subagent/engine 入口 clamp 到 [1,20]
    initial_capital: float
    constraints: dict
    provider: str | None

    # ── setup 产物（prepare 节点写；resume 时磁盘路径不可信，见 engine.rebuild）──
    data_ready: bool
    data_summary: dict
    tmpdir: str
    train_paths: dict[str, str]
    val_paths: dict[str, str]

    # ── 循环控制（★原为 start_session 局部变量，必须持久化）──
    current_iteration: int               # 下一轮要执行的 i（advance 自增）
    phase: str                           # "explore" | "refine"
    no_improve_count: int                # ★原 engine.py:135
    fail_streak: int                     # ★原 engine.py:136
    stop_reason: str | None           # None | "max_iter" | "no_improve" | "fail_streak"

    # ── 单轮临时（每轮 generate→evaluate 覆写）──
    current_code: str
    current_full_response: str
    current_model: str
    current_tokens: int
    last_gen_ok: bool
    ast_ok: bool
    ast_violations: list
    backtest_result: dict                # sandbox result.json，天然 JSON
    backtest_ok: bool
    execution_time: float
    last_eval: dict | None            # {train_metrics, val_metrics, overfit_score, overfit_warnings, composite_score}

    # ── 会话累积（== SessionState 的 JSON 友好字段）──
    messages: list[dict]
    iterations: list[dict]               # IterationRecord 的 dataclasses.asdict
    best_iteration: int | None
    best_val_sharpe: float               # 初始 -999.0
    best_composite_score: float          # 初始 -999.0
    total_tokens: int
    total_cost: float
    status: str                          # running | completed | failed
