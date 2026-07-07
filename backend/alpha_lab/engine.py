"""
AlphaLabEngine — 迭代主循环
"""
import os
import json
import time
import tempfile
from typing import List, Dict, Optional, Generator

import pandas as pd
from loguru import logger

from alpha_lab.sandbox import Sandbox, _ndjson
from alpha_lab.code_generator import CodeGenerator, _get_all_provider_configs
from alpha_lab.evaluator import Evaluator, EvaluationResult
from alpha_lab.session_manager import SessionManager, SessionState, IterationRecord
from alpha_lab.strategy_store import StrategyStore

from data_engine.engine import DataEngine
from strategy.indicators import TechnicalIndicators


class AlphaLabEngine:
    """Alpha Lab 迭代引擎"""

    # 探索期/精炼期分界线
    EXPLORE_ROUNDS = 5
    # max_iterations 硬上限：所有入口（MoneyBill subagent / knowledge 的 ideas 回测路由）
    # 都汇聚到 start_session，在此统一钳制才能真正兜住。20 轮足够 explore(5)+refine，
    # 再多无实际收益，且无美元熔断时轮数是唯一的烧钱闸门。
    MAX_ITERATIONS_CAP = 20

    def __init__(self):
        self.session_manager = SessionManager()
        self.code_generator = CodeGenerator()
        self.sandbox = Sandbox()
        self.evaluator = Evaluator()
        self.strategy_store = StrategyStore()

    @staticmethod
    def get_providers() -> List[Dict]:
        """获取所有可用的 AI provider 列表"""
        configs = _get_all_provider_configs()
        result = []
        for key, cfg in configs.items():
            result.append({
                "id": key,
                "label": cfg.get("label", key),
                "available": cfg.get("available", False),
                "explore_model": cfg.get("explore_model", cfg.get("model", "")),
                "refine_model": cfg.get("refine_model", cfg.get("model", "")),
            })
        return result

    def start_session(
        self,
        target_symbols: List[str],
        optimization_goal: str,
        data_start: str,
        data_end: str,
        max_iterations: int = 15,
        initial_capital: float = 1000000.0,
        constraints: Optional[Dict] = None,
        provider: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """
        启动新 Alpha Lab 会话（同步生成器，yield NDJSON 行）
        """
        # 按本次请求的 provider 重建 code_generator。
        # _engine 是模块单例，必须每次都反映本次请求：传 provider 用之，
        # 不传则复位为 .env 默认，避免上一次的 provider 残留串到下一次请求（误走付费后端）。
        if provider:
            self.code_generator = CodeGenerator(provider=provider)
            logger.info(f"Alpha Lab 切换 provider: {provider}")
        else:
            self.code_generator = CodeGenerator()  # 复位为 .env ALPHA_LAB_PROVIDER 默认

        # max_iterations 统一钳制（唯一汇聚点，兜住所有入口的越界值）
        try:
            max_iterations = int(max_iterations)
        except (TypeError, ValueError):
            max_iterations = 8
        max_iterations = max(1, min(max_iterations, self.MAX_ITERATIONS_CAP))

        # 1. 创建会话
        session = self.session_manager.create(
            target_symbols=target_symbols,
            optimization_goal=optimization_goal,
            data_range=(data_start, data_end),
            max_iterations=max_iterations,
            initial_capital=initial_capital,
            constraints=constraints,
        )
        yield _ndjson({"event": "session_created", "session_id": session.id})

        # 持久化
        self.strategy_store.save_session(
            session_id=session.id,
            target_symbols=target_symbols,
            optimization_goal=optimization_goal,
            data_start=data_start,
            data_end=data_end,
            max_iterations=max_iterations,
            initial_capital=initial_capital,
            constraints=constraints,
        )

        # 2. 准备数据
        yield _ndjson({"event": "preparing_data", "message": "正在准备数据和计算指标..."})
        try:
            train_paths, val_paths, data_summary = self._prepare_data(
                session, target_symbols, data_start, data_end
            )
        except Exception as e:
            yield _ndjson({"event": "error", "message": f"数据准备失败: {e}"})
            session.status = "failed"
            self.strategy_store.update_session_status(session.id, "failed")
            return

        yield _ndjson({
            "event": "data_ready",
            "message": f"数据准备完成，训练集 {data_summary['train_days']} 天，验证集 {data_summary['val_days']} 天",
        })

        # 3. 构建初始 messages
        symbol = target_symbols[0]  # 当前先支持单股票
        session.messages = self.code_generator.build_initial_messages(
            symbols=target_symbols,
            optimization_goal=optimization_goal,
            data_summary=data_summary,
            constraints=constraints,
        )

        # 4. 迭代循环
        no_improve_count = 0   # 只计成功但未改进的轮次
        fail_streak = 0        # 连续失败计数（代码/回测错误）

        for i in range(1, max_iterations + 1):
            phase = "explore" if i <= self.EXPLORE_ROUNDS else "refine"
            model = self.code_generator.get_model_for_phase(phase)
            session.phase = phase
            session.current_iteration = i  # 无论成功失败都记录实际迭代轮数

            yield _ndjson({
                "event": "iteration_start",
                "iteration": i,
                "model": model,
                "phase": phase,
            })

            try:
                eval_result = None
                for event in self._run_iteration(
                    session, i, model, symbol,
                    train_paths[symbol], val_paths[symbol],
                ):
                    if isinstance(event, EvaluationResult):
                        eval_result = event
                    else:
                        yield event

                if eval_result is None:
                    # 迭代失败（代码错误/AST拒绝/回测失败），不算进早停
                    fail_streak += 1
                    continue

                fail_streak = 0  # 成功执行，重置失败连击

                # 更新最佳
                val_sharpe = self.evaluator._get_sharpe(eval_result.val_metrics)
                is_best = eval_result.composite_score > session.best_composite_score
                if is_best:
                    self.session_manager.update_best(session, i, val_sharpe, eval_result.composite_score)
                    no_improve_count = 0
                else:
                    no_improve_count += 1

                yield _ndjson({
                    "event": "iteration_complete",
                    "iteration": i,
                    "train_sharpe": round(self.evaluator._get_sharpe(eval_result.train_metrics), 3),
                    "val_sharpe": round(val_sharpe, 3),
                    "overfit_score": round(eval_result.overfit_score, 3),
                    "composite_score": round(eval_result.composite_score, 4),
                    "is_best": is_best,
                })

            except Exception as e:
                logger.error(f"[{session.id}] 迭代 {i} 异常: {e}")
                yield _ndjson({"event": "iteration_error", "iteration": i, "error": str(e)})
                fail_streak += 1

            # 检查 phase 切换
            if i == self.EXPLORE_ROUNDS:
                yield _ndjson({"event": "phase_change", "from": "explore", "to": "refine", "iteration": i + 1})

            # 早停：连续 5 轮成功执行但无改进，才触发
            if no_improve_count >= 5 and i >= self.EXPLORE_ROUNDS:
                yield _ndjson({"event": "early_stop", "reason": f"连续 {no_improve_count} 轮无改进"})
                break

            # 连续失败太多次也停（避免一直烧 token）
            if fail_streak >= 5:
                yield _ndjson({"event": "early_stop", "reason": f"连续 {fail_streak} 轮执行失败"})
                break

        # 5. 完成
        session.status = "completed"
        self.strategy_store.update_session_status(
            session.id,
            status="completed",
            total_iterations=session.current_iteration,
            best_iteration=session.best_iteration,
            best_sharpe=session.best_val_sharpe if session.best_val_sharpe > -999 else None,
            best_composite_score=session.best_composite_score if session.best_composite_score > -999 else None,
            cost_usd=round(session.total_cost, 4),
            total_tokens=session.total_tokens,
        )

        yield _ndjson({
            "event": "session_complete",
            "session_id": session.id,
            "best_iteration": session.best_iteration,
            "best_sharpe": round(session.best_val_sharpe, 3) if session.best_val_sharpe > -999 else None,
            "total_iterations": session.current_iteration,
            "total_cost_usd": round(session.total_cost, 4),
        })

        # 清理临时文件
        self._cleanup_temp_data(train_paths, val_paths)

    def _run_iteration(
        self,
        session: SessionState,
        iteration: int,
        model: str,
        symbol: str,
        train_data_path: str,
        val_data_path: str,
    ) -> Generator:
        """
        执行单轮迭代

        Yields:
            NDJSON 字符串 或 EvaluationResult
        """
        # 1. 如果不是第一轮，构建迭代 prompt
        if iteration > 1 and session.iterations:
            prev = session.iterations[-1]
            session.messages = self.code_generator.build_iteration_messages(
                messages=session.messages,
                iteration=iteration,
                prev_train_metrics=prev.train_metrics,
                prev_val_metrics=prev.val_metrics,
                prev_overfit_score=prev.overfit_score,
                prev_overfit_warnings=[],  # 简化
                prev_composite_score=prev.composite_score,
                best_iteration=session.best_iteration,
                best_val_sharpe=session.best_val_sharpe if session.best_val_sharpe > -999 else None,
                best_composite_score=session.best_composite_score if session.best_composite_score > -999 else None,
                phase=session.phase,
            )

        # 2. AI 生成代码
        yield _ndjson({"event": "generating_code", "iteration": iteration, "message": "AI 正在生成策略代码..."})

        start_time = time.time()
        try:
            code, full_response, tokens = self.code_generator.generate(
                messages=session.messages,
                model=model,
                temperature=0.7 if session.phase == "explore" else 0.4,
            )
        except Exception as e:
            yield _ndjson({"event": "code_error", "iteration": iteration, "error": str(e)})
            return

        # 记录 AI 回复到对话历史
        session.messages.append({"role": "assistant", "content": full_response})
        self.session_manager.add_cost(session, tokens, model)

        yield _ndjson({
            "event": "code_generated",
            "iteration": iteration,
            "code_preview": code[:200] + "..." if len(code) > 200 else code,
            "tokens": tokens,
        })

        # 3. AST 安全检查
        is_safe, violations = self.sandbox.ast_check(code)
        if not is_safe:
            yield _ndjson({
                "event": "ast_rejected",
                "iteration": iteration,
                "violations": violations,
            })
            self.strategy_store.save_strategy(
                session_id=session.id, iteration=iteration,
                code=code, ai_reasoning=full_response,
                train_metrics={}, val_metrics={},
                overfit_score=0, composite_score=-999,
                status="rejected", error_message=f"AST 检查失败: {violations}",
            )
            self.strategy_store.add_log(session.id, "ast_rejected", str(violations), iteration=iteration, level="WARN")
            return

        yield _ndjson({"event": "ast_check_passed", "iteration": iteration})

        # 4. 沙箱执行回测
        yield _ndjson({"event": "backtest_running", "iteration": iteration, "symbol": symbol})

        backtest_result = self.sandbox.execute_backtest(
            strategy_code=code,
            symbol=symbol,
            train_data_path=train_data_path,
            val_data_path=val_data_path,
            initial_capital=session.initial_capital,
        )

        execution_time = round(time.time() - start_time, 2)

        if not backtest_result.get("success"):
            error_msg = backtest_result.get("error", "未知回测错误")
            yield _ndjson({
                "event": "backtest_failed",
                "iteration": iteration,
                "error": error_msg[:500],
            })
            self.strategy_store.save_strategy(
                session_id=session.id, iteration=iteration,
                code=code, ai_reasoning=full_response,
                train_metrics={}, val_metrics={},
                overfit_score=0, composite_score=-999,
                status="failed", error_message=error_msg[:1000],
                execution_time=execution_time,
            )
            self.strategy_store.add_log(session.id, "backtest_failed", error_msg[:500], iteration=iteration, level="ERROR")
            return

        yield _ndjson({"event": "backtest_done", "iteration": iteration})

        # 5. 评估
        eval_result = self.evaluator.evaluate(
            iteration=iteration,
            code=code,
            backtest_result=backtest_result,
            optimization_goal=session.optimization_goal,
        )

        yield _ndjson({
            "event": "evaluation_done",
            "iteration": iteration,
            "train_sharpe": round(self.evaluator._get_sharpe(eval_result.train_metrics), 3),
            "val_sharpe": round(self.evaluator._get_sharpe(eval_result.val_metrics), 3),
            "overfit_score": round(eval_result.overfit_score, 3),
            "composite_score": round(eval_result.composite_score, 4),
            "overfit_warnings": eval_result.overfit_warnings,
        })

        # 6. 持久化
        self.strategy_store.save_strategy(
            session_id=session.id,
            iteration=iteration,
            code=code,
            ai_reasoning=full_response,
            train_metrics=eval_result.train_metrics,
            val_metrics=eval_result.val_metrics,
            overfit_score=eval_result.overfit_score,
            composite_score=eval_result.composite_score,
            execution_time=execution_time,
        )
        self.strategy_store.add_log(
            session.id, "iteration_complete",
            f"Sharpe={self.evaluator._get_sharpe(eval_result.val_metrics):.3f}, Score={eval_result.composite_score:.4f}",
            iteration=iteration,
        )

        # 记录到内存
        self.session_manager.add_iteration(session, IterationRecord(
            iteration=iteration,
            code=code,
            train_metrics=eval_result.train_metrics,
            val_metrics=eval_result.val_metrics,
            overfit_score=eval_result.overfit_score,
            composite_score=eval_result.composite_score,
            model=model,
            tokens=tokens,
        ))

        # 返回评估结果给主循环
        yield eval_result

    def _prepare_data(
        self,
        session: SessionState,
        symbols: List[str],
        data_start: str,
        data_end: str,
    ) -> tuple:
        """
        准备训练/验证数据，保存为 parquet 临时文件

        Returns:
            (train_paths: Dict[str, str], val_paths: Dict[str, str], data_summary: Dict)
        """
        data_engine = DataEngine()
        train_paths = {}
        val_paths = {}
        data_summary = {}

        tmpdir = tempfile.mkdtemp(prefix="alab_data_")

        for symbol in symbols:
            logger.info(f"准备数据: {symbol} ({data_start} ~ {data_end})")
            df = data_engine.get_daily_data(
                symbol=symbol,
                start_date=data_start,
                end_date=data_end,
            )

            if df.empty:
                raise ValueError(f"{symbol} 在 {data_start}~{data_end} 期间无数据")

            # 计算技术指标
            df = TechnicalIndicators.calculate_all_indicators(df)

            # 时序分割：前 70% 训练，后 30% 验证
            split_idx = int(len(df) * 0.7)
            train_df = df.iloc[:split_idx].copy()
            val_df = df.iloc[split_idx:].copy()

            # 保存为 CSV 临时文件
            train_path = os.path.join(tmpdir, f"{symbol}_train.csv")
            val_path = os.path.join(tmpdir, f"{symbol}_val.csv")
            train_df.to_csv(train_path, index=True)
            val_df.to_csv(val_path, index=True)

            train_paths[symbol] = train_path
            val_paths[symbol] = val_path

            # 数据摘要（累积多股票信息）
            sym_summary = {
                "train_days": len(train_df),
                "val_days": len(val_df),
                "total_days": len(df),
                "price_range": f"{df['close'].min():.2f} ~ {df['close'].max():.2f}",
                "avg_volume": f"{df['volume'].mean():,.0f}",
                "latest_price": f"{df['close'].iloc[-1]:.2f}",
            }
            if not data_summary:
                data_summary = sym_summary
            else:
                # 多股票时汇总：天数取最小值，价格范围合并
                data_summary["train_days"] = min(data_summary["train_days"], sym_summary["train_days"])
                data_summary["val_days"] = min(data_summary["val_days"], sym_summary["val_days"])
                data_summary["total_days"] = min(data_summary["total_days"], sym_summary["total_days"])
                data_summary["price_range"] += f" | {symbol}: {sym_summary['price_range']}"
                data_summary["avg_volume"] += f" | {symbol}: {sym_summary['avg_volume']}"
                data_summary["latest_price"] += f" | {symbol}: {sym_summary['latest_price']}"

        logger.info(f"数据准备完成: train={data_summary['train_days']}天, val={data_summary['val_days']}天")
        return train_paths, val_paths, data_summary

    @staticmethod
    def _cleanup_temp_data(train_paths: Dict, val_paths: Dict):
        """清理临时数据文件"""
        import shutil
        cleaned = set()
        for path in list(train_paths.values()) + list(val_paths.values()):
            parent = os.path.dirname(path)
            if parent not in cleaned and os.path.exists(parent):
                try:
                    shutil.rmtree(parent)
                    cleaned.add(parent)
                except Exception as e:
                    logger.warning(f"清理临时目录失败: {e}")

    def get_sessions(self, status: Optional[str] = None) -> List[Dict]:
        """获取会话列表（优先从数据库）"""
        return self.strategy_store.get_sessions(status=status)

    def get_session_detail(self, session_id: str) -> Optional[Dict]:
        """获取会话详情 + 策略列表"""
        sessions = self.strategy_store.get_sessions()
        session_info = next((s for s in sessions if s["id"] == session_id), None)
        if not session_info:
            return None
        strategies = self.strategy_store.get_session_strategies(session_id)
        session_info["strategies"] = strategies
        return session_info

    def get_strategy_detail(self, strategy_id: str) -> Optional[Dict]:
        """获取策略详情"""
        return self.strategy_store.get_strategy(strategy_id)

    def delete_session(self, session_id: str) -> bool:
        """删除会话及关联数据"""
        return self.strategy_store.delete_session(session_id)
