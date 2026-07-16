"""
alpha_lab 图节点 —— 把 `AlphaLabEngine.start_session` 循环体逐段搬进 LangGraph 节点。

设计要点（对齐 CODING_STANDARDS §6 + 计划风险清单）：
- **失败转 flag 不抛异常**：generate/ast_check/backtest 各自把失败落成 `last_gen_ok`/
  `ast_ok`/`backtest_ok=False`，交给 `advance` 统一处理。这样超步内不抛异常（免疫「超步
  异常回滚整个 state 更新」坑），且 checkpoint 记录到失败态而非回滚丢失。唯一的不可恢复
  错误是 prepare 数据准备失败 → 走 finalize 收尾（status=failed）。
- **失败即 continue 语义**：`advance` 在失败轮只 `fail_streak+=1` 并跳过 iteration_complete /
  phase_change / 早停判定，与旧引擎 `eval_result is None → continue`（engine.py:162-165）逐字一致。
  因此 fail_streak≥5 早停在纯失败流下不可达（旧引擎亦然），持续失败会跑满 max_iterations。
- **事件流兼容**：节点用 `get_stream_writer()` 逐条 emit 与旧引擎 `_ndjson(...)` 同名同字段的
  事件 dict，engine 层原样转 NDJSON，前端/subagent 零改动。
- **all 状态突变集中在 advance**：best/计数器/phase 切换/早停/current_iteration 自增只在此节点，
  随节点边界原子落盘——这是「evaluate 成功但 advance 前崩溃可安全 resume」的关键。
"""
import os
import time

from langgraph.config import get_stream_writer
from loguru import logger

from alpha_lab.code_generator import CodeGenerator
from alpha_lab.data_prep import prepare_data
from alpha_lab.evaluator import Evaluator
from alpha_lab.graph.state import AlphaLabGraphState
from alpha_lab.sandbox import Sandbox
from alpha_lab.session_manager import SessionManager
from alpha_lab.strategy_store import StrategyStore

# 探索/精炼分界，单一真源沿用引擎常量
EXPLORE_ROUNDS = 5


def _emit(payload: dict) -> None:
    """向 custom stream 写一条事件（非流式上下文下 writer 为空则静默跳过）。"""
    writer = get_stream_writer()
    if writer is not None:
        writer(payload)


class AlphaLabNodes:
    """持有各组件实例的图节点集合（每个方法即一个图节点）。"""

    def __init__(
        self,
        code_generator: CodeGenerator,
        sandbox: Sandbox,
        evaluator: Evaluator,
        strategy_store: StrategyStore,
    ) -> None:
        self.code_generator = code_generator
        self.sandbox = sandbox
        self.evaluator = evaluator
        self.strategy_store = strategy_store

    # ── prepare：循环外一次性数据准备 + 初始 messages（engine.py:108-132）──
    def prepare(self, state: AlphaLabGraphState) -> dict:
        _emit({"event": "preparing_data", "message": "正在准备数据和计算指标..."})
        symbols = state["target_symbols"]
        try:
            train_paths, val_paths, data_summary = prepare_data(
                symbols, state["data_start"], state["data_end"],
            )
        except (ValueError, KeyError, OSError) as e:
            _emit({"event": "error", "message": f"数据准备失败: {e}"})
            return {"data_ready": False, "status": "failed", "stop_reason": "data_error"}

        _emit({
            "event": "data_ready",
            "message": f"数据准备完成，训练集 {data_summary['train_days']} 天，验证集 {data_summary['val_days']} 天",
        })

        tmpdir = os.path.dirname(next(iter(train_paths.values())))
        messages = self.code_generator.build_initial_messages(
            symbols=symbols,
            optimization_goal=state["optimization_goal"],
            data_summary=data_summary,
            constraints=state.get("constraints") or {},
        )
        return {
            "data_ready": True,
            "train_paths": train_paths,
            "val_paths": val_paths,
            "data_summary": data_summary,
            "tmpdir": tmpdir,
            "messages": messages,
            "symbol": symbols[0],  # 当前先支持单股票
        }

    # ── generate：设 phase/model + 迭代 prompt + LLM 生成（engine.py:139-149, 247-287）──
    def generate(self, state: AlphaLabGraphState) -> dict:
        i = state["current_iteration"]
        phase = "explore" if i <= EXPLORE_ROUNDS else "refine"
        model = self.code_generator.get_model_for_phase(phase)

        _emit({"event": "iteration_start", "iteration": i, "model": model, "phase": phase})

        messages = list(state["messages"])
        iterations = state.get("iterations") or []
        # 非首轮：把上一轮指标喂回，构成 evaluator-optimizer 反馈（engine.py:248-262）
        if i > 1 and iterations:
            prev = iterations[-1]
            best_sharpe = state.get("best_val_sharpe", -999.0)
            best_comp = state.get("best_composite_score", -999.0)
            messages = self.code_generator.build_iteration_messages(
                messages=messages,
                iteration=i,
                prev_train_metrics=prev["train_metrics"],
                prev_val_metrics=prev["val_metrics"],
                prev_overfit_score=prev["overfit_score"],
                prev_overfit_warnings=[],  # 简化（对齐旧引擎）
                prev_composite_score=prev["composite_score"],
                best_iteration=state.get("best_iteration"),
                best_val_sharpe=best_sharpe if best_sharpe > -999 else None,
                best_composite_score=best_comp if best_comp > -999 else None,
                phase=phase,
            )

        _emit({"event": "generating_code", "iteration": i, "message": "AI 正在生成策略代码..."})
        try:
            code, full_response, tokens = self.code_generator.generate(
                messages=messages,
                model=model,
                temperature=0.7 if phase == "explore" else 0.4,
            )
        except Exception as e:  # noqa: BLE001 — 生成失败转 flag，交 advance 计 fail_streak
            _emit({"event": "code_error", "iteration": i, "error": str(e)})
            return {"phase": phase, "current_model": model, "last_gen_ok": False,
                    "ast_ok": False, "backtest_ok": False, "last_eval": None}

        messages.append({"role": "assistant", "content": full_response})
        rate = SessionManager.COST_RATES.get(model, 0.0)  # 未知模型（本地）费率 0
        _emit({
            "event": "code_generated",
            "iteration": i,
            "code_preview": code[:200] + "..." if len(code) > 200 else code,
            "tokens": tokens,
        })
        return {
            "phase": phase,
            "current_model": model,
            "current_code": code,
            "current_full_response": full_response,
            "current_tokens": tokens,
            "messages": messages,
            "total_tokens": state.get("total_tokens", 0) + tokens,
            "total_cost": state.get("total_cost", 0.0) + tokens * rate / 1000,
            "last_gen_ok": True,
            "ast_ok": False,      # 待 ast_check 置真
            "backtest_ok": False, # 待 backtest 置真
            "last_eval": None,
        }

    # ── ast_check：AST 安全检查（engine.py:289-307）──
    def ast_check(self, state: AlphaLabGraphState) -> dict:
        if not state.get("last_gen_ok"):
            return {}  # 生成已失败，直通到 advance
        i = state["current_iteration"]
        code = state["current_code"]
        is_safe, violations = self.sandbox.ast_check(code)
        if not is_safe:
            _emit({"event": "ast_rejected", "iteration": i, "violations": violations})
            self.strategy_store.save_strategy(
                session_id=state["session_id"], iteration=i,
                code=code, ai_reasoning=state["current_full_response"],
                train_metrics={}, val_metrics={},
                overfit_score=0, composite_score=-999,
                status="rejected", error_message=f"AST 检查失败: {violations}",
            )
            self.strategy_store.add_log(
                state["session_id"], "ast_rejected", str(violations), iteration=i, level="WARN")
            return {"ast_ok": False, "ast_violations": violations}
        _emit({"event": "ast_check_passed", "iteration": i})
        return {"ast_ok": True}

    # ── backtest：沙箱子进程回测（engine.py:309-340）──
    def backtest(self, state: AlphaLabGraphState) -> dict:
        if not state.get("ast_ok"):
            return {}
        i = state["current_iteration"]
        _emit({"event": "backtest_running", "iteration": i, "symbol": state["symbol"]})
        start_time = time.time()
        result = self.sandbox.execute_backtest(
            strategy_code=state["current_code"],
            symbol=state["symbol"],
            train_data_path=state["train_paths"][state["symbol"]],
            val_data_path=state["val_paths"][state["symbol"]],
            initial_capital=state["initial_capital"],
        )
        execution_time = round(time.time() - start_time, 2)
        if not result.get("success"):
            error_msg = result.get("error", "未知回测错误")
            _emit({"event": "backtest_failed", "iteration": i, "error": error_msg[:500]})
            self.strategy_store.save_strategy(
                session_id=state["session_id"], iteration=i,
                code=state["current_code"], ai_reasoning=state["current_full_response"],
                train_metrics={}, val_metrics={},
                overfit_score=0, composite_score=-999,
                status="failed", error_message=error_msg[:1000],
                execution_time=execution_time,
            )
            self.strategy_store.add_log(
                state["session_id"], "backtest_failed", error_msg[:500], iteration=i, level="ERROR")
            return {"backtest_ok": False, "execution_time": execution_time}
        _emit({"event": "backtest_done", "iteration": i})
        return {"backtest_ok": True, "backtest_result": result, "execution_time": execution_time}

    # ── evaluate：纯代码评估 + 持久化 + 追加迭代记录（engine.py:342-391）──
    def evaluate(self, state: AlphaLabGraphState) -> dict:
        if not state.get("backtest_ok"):
            return {}
        i = state["current_iteration"]
        result = self.evaluator.evaluate(
            iteration=i,
            code=state["current_code"],
            backtest_result=state["backtest_result"],
            optimization_goal=state["optimization_goal"],
        )
        _emit({
            "event": "evaluation_done",
            "iteration": i,
            "train_sharpe": round(self.evaluator._get_sharpe(result.train_metrics), 3),
            "val_sharpe": round(self.evaluator._get_sharpe(result.val_metrics), 3),
            "overfit_score": round(result.overfit_score, 3),
            "composite_score": round(result.composite_score, 4),
            "overfit_warnings": result.overfit_warnings,
        })
        self.strategy_store.save_strategy(
            session_id=state["session_id"], iteration=i,
            code=state["current_code"], ai_reasoning=state["current_full_response"],
            train_metrics=result.train_metrics, val_metrics=result.val_metrics,
            overfit_score=result.overfit_score, composite_score=result.composite_score,
            execution_time=state.get("execution_time", 0.0),
        )
        self.strategy_store.add_log(
            state["session_id"], "iteration_complete",
            f"Sharpe={self.evaluator._get_sharpe(result.val_metrics):.3f}, Score={result.composite_score:.4f}",
            iteration=i,
        )
        # 追加迭代记录（IterationRecord 的 dict 形式，可序列化进 checkpoint）
        iterations = list(state.get("iterations") or [])
        iterations.append({
            "iteration": i,
            "code": state["current_code"],
            "train_metrics": result.train_metrics,
            "val_metrics": result.val_metrics,
            "overfit_score": result.overfit_score,
            "composite_score": result.composite_score,
            "model": state["current_model"],
            "tokens": state.get("current_tokens", 0),
        })
        return {
            "iterations": iterations,
            "last_eval": {
                "train_metrics": result.train_metrics,
                "val_metrics": result.val_metrics,
                "overfit_score": result.overfit_score,
                "overfit_warnings": result.overfit_warnings,
                "composite_score": result.composite_score,
            },
        }

    # ── advance：纯代码汇聚——best/计数器/phase切换/早停/游标自增（engine.py:162-205）──
    def advance(self, state: AlphaLabGraphState) -> dict:
        i = state["current_iteration"]
        success = (
            state.get("last_gen_ok") and state.get("ast_ok")
            and state.get("backtest_ok") and state.get("last_eval") is not None
        )
        no_improve = state.get("no_improve_count", 0)
        fail_streak = state.get("fail_streak", 0)
        best_iter = state.get("best_iteration")
        best_sharpe = state.get("best_val_sharpe", -999.0)
        best_comp = state.get("best_composite_score", -999.0)

        # 失败轮：只累计 fail_streak 并 continue（跳过 iteration_complete/phase_change/早停）
        if not success:
            return {"fail_streak": fail_streak + 1, "current_iteration": i + 1}

        fail_streak = 0
        ev = state["last_eval"]
        val_sharpe = self.evaluator._get_sharpe(ev["val_metrics"])
        is_best = ev["composite_score"] > best_comp
        if is_best:
            best_iter, best_sharpe, best_comp = i, val_sharpe, ev["composite_score"]
            no_improve = 0
            logger.info(f"[{state['session_id']}] 新最佳: iter={i}, sharpe={val_sharpe:.3f}, score={best_comp:.4f}")
        else:
            no_improve += 1

        _emit({
            "event": "iteration_complete",
            "iteration": i,
            "train_sharpe": round(self.evaluator._get_sharpe(ev["train_metrics"]), 3),
            "val_sharpe": round(val_sharpe, 3),
            "overfit_score": round(ev["overfit_score"], 3),
            "composite_score": round(ev["composite_score"], 4),
            "is_best": is_best,
        })

        # phase 切换提示（engine.py:194-195）
        if i == EXPLORE_ROUNDS:
            _emit({"event": "phase_change", "from": "explore", "to": "refine", "iteration": i + 1})

        # 早停判定（engine.py:197-205）
        stop_reason: str | None = None
        if no_improve >= 5 and i >= EXPLORE_ROUNDS:
            _emit({"event": "early_stop", "reason": f"连续 {no_improve} 轮无改进"})
            stop_reason = "no_improve"
        elif fail_streak >= 5:  # 成功轮此处 fail_streak 恒 0，与旧引擎同为不可达分支
            _emit({"event": "early_stop", "reason": f"连续 {fail_streak} 轮执行失败"})
            stop_reason = "fail_streak"

        return {
            "fail_streak": 0,
            "no_improve_count": no_improve,
            "best_iteration": best_iter,
            "best_val_sharpe": best_sharpe,
            "best_composite_score": best_comp,
            "stop_reason": stop_reason,
            "current_iteration": i + 1,
        }

    # ── finalize：收尾——更新库状态 + session_complete（engine.py:207-227）──
    def finalize(self, state: AlphaLabGraphState) -> dict:
        session_id = state["session_id"]
        # 数据准备失败路径：只更 failed 状态，不发 session_complete（subagent 归类 internal_error）
        if state.get("status") == "failed":
            self.strategy_store.update_session_status(session_id, "failed")
            return {"status": "failed"}

        total_iterations = state["current_iteration"] - 1  # advance 已把游标推过末轮
        best_sharpe = state.get("best_val_sharpe", -999.0)
        best_comp = state.get("best_composite_score", -999.0)
        self.strategy_store.update_session_status(
            session_id,
            status="completed",
            total_iterations=total_iterations,
            best_iteration=state.get("best_iteration"),
            best_sharpe=best_sharpe if best_sharpe > -999 else None,
            best_composite_score=best_comp if best_comp > -999 else None,
            cost_usd=round(state.get("total_cost", 0.0), 4),
            total_tokens=state.get("total_tokens", 0),
        )
        _emit({
            "event": "session_complete",
            "session_id": session_id,
            "best_iteration": state.get("best_iteration"),
            "best_sharpe": round(best_sharpe, 3) if best_sharpe > -999 else None,
            "total_iterations": total_iterations,
            "total_cost_usd": round(state.get("total_cost", 0.0), 4),
        })
        return {"status": "completed"}


# ── 条件边路由（纯函数，不写 state）──
def route_after_prepare(state: AlphaLabGraphState) -> str:
    return "generate" if state.get("data_ready") else "finalize"


def route_after_advance(state: AlphaLabGraphState) -> str:
    if state.get("stop_reason"):
        return "finalize"
    if state["current_iteration"] > state["max_iterations"]:
        return "finalize"
    return "generate"
