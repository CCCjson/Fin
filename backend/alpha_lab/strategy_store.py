"""
策略持久化 — 保存到数据库
"""
import json
from typing import List, Optional, Dict
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import AlphaLabSession, AlphaLabStrategy, AlphaLabLog


class StrategyStore:
    """策略持久化存储"""

    def save_session(
        self,
        session_id: str,
        target_symbols: List[str],
        optimization_goal: str,
        data_start: str,
        data_end: str,
        max_iterations: int,
        initial_capital: float,
        constraints: Optional[Dict] = None,
    ):
        """持久化会话信息"""
        db = get_session()
        try:
            record = AlphaLabSession(
                id=session_id,
                target_symbols=json.dumps(target_symbols, ensure_ascii=False),
                optimization_goal=optimization_goal,
                data_start=data_start,
                data_end=data_end,
                max_iterations=max_iterations,
                initial_capital=initial_capital,
                constraints=json.dumps(constraints or {}, ensure_ascii=False),
            )
            db.add(record)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"保存会话失败: {e}")
        finally:
            db.close()

    def update_session_status(
        self,
        session_id: str,
        status: str,
        total_iterations: int = 0,
        best_iteration: Optional[int] = None,
        best_sharpe: Optional[float] = None,
        best_composite_score: Optional[float] = None,
        cost_usd: float = 0.0,
        total_tokens: int = 0,
    ):
        """更新会话状态"""
        db = get_session()
        try:
            record = db.query(AlphaLabSession).filter(AlphaLabSession.id == session_id).first()
            if record:
                record.status = status
                record.total_iterations = total_iterations
                record.best_iteration = best_iteration
                record.best_sharpe = best_sharpe
                record.best_composite_score = best_composite_score
                record.cost_usd = cost_usd
                record.total_tokens = total_tokens
                if status in ("completed", "failed"):
                    from sqlalchemy.sql import func
                    record.completed_at = func.now()
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"更新会话状态失败: {e}")
        finally:
            db.close()

    def save_strategy(
        self,
        session_id: str,
        iteration: int,
        code: str,
        ai_reasoning: str,
        train_metrics: Dict,
        val_metrics: Dict,
        overfit_score: float,
        composite_score: float,
        status: str = "completed",
        error_message: Optional[str] = None,
        execution_time: float = 0.0,
    ) -> str:
        """保存一轮策略"""
        strategy_id = f"{session_id}_iter{iteration}"
        db = get_session()
        try:
            record = AlphaLabStrategy(
                id=strategy_id,
                session_id=session_id,
                iteration=iteration,
                code=code,
                ai_reasoning=ai_reasoning,
                train_metrics=json.dumps(train_metrics, ensure_ascii=False, default=str),
                val_metrics=json.dumps(val_metrics, ensure_ascii=False, default=str),
                overfit_score=overfit_score,
                composite_score=composite_score,
                status=status,
                error_message=error_message,
                execution_time=execution_time,
            )
            db.add(record)
            db.commit()
            return strategy_id
        except Exception as e:
            db.rollback()
            logger.error(f"保存策略失败: {e}")
            return strategy_id
        finally:
            db.close()

    def add_log(
        self,
        session_id: str,
        event: str,
        message: str = "",
        iteration: Optional[int] = None,
        level: str = "INFO",
        details: Optional[Dict] = None,
    ):
        """写日志"""
        db = get_session()
        try:
            log = AlphaLabLog(
                session_id=session_id,
                iteration=iteration,
                level=level,
                event=event,
                message=message,
                details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
            )
            db.add(log)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"写日志失败: {e}")
        finally:
            db.close()

    def get_sessions(self, status: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """查询会话列表"""
        db = get_session()
        try:
            q = db.query(AlphaLabSession).order_by(AlphaLabSession.created_at.desc())
            if status:
                q = q.filter(AlphaLabSession.status == status)
            records = q.limit(limit).all()
            return [
                {
                    "id": r.id,
                    "target_symbols": json.loads(r.target_symbols) if r.target_symbols else [],
                    "optimization_goal": r.optimization_goal,
                    "data_start": r.data_start,
                    "data_end": r.data_end,
                    "status": r.status,
                    "total_iterations": r.total_iterations or 0,
                    "best_iteration": r.best_iteration,
                    "best_sharpe": r.best_sharpe,
                    "best_composite_score": r.best_composite_score,
                    "cost_usd": r.cost_usd or 0,
                    "total_tokens": r.total_tokens or 0,
                    "created_at": str(r.created_at) if r.created_at else None,
                    "completed_at": str(r.completed_at) if r.completed_at else None,
                }
                for r in records
            ]
        finally:
            db.close()

    def get_session_strategies(self, session_id: str) -> List[Dict]:
        """查询会话所有策略"""
        db = get_session()
        try:
            records = (
                db.query(AlphaLabStrategy)
                .filter(AlphaLabStrategy.session_id == session_id)
                .order_by(AlphaLabStrategy.iteration)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "iteration": r.iteration,
                    "code": r.code,
                    "ai_reasoning": r.ai_reasoning,
                    "train_metrics": json.loads(r.train_metrics) if r.train_metrics else {},
                    "val_metrics": json.loads(r.val_metrics) if r.val_metrics else {},
                    "overfit_score": r.overfit_score,
                    "composite_score": r.composite_score,
                    "status": r.status,
                    "error_message": r.error_message,
                    "execution_time": r.execution_time,
                    "deployed": bool(r.deployed),
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in records
            ]
        finally:
            db.close()

    def get_strategy(self, strategy_id: str) -> Optional[Dict]:
        """查询单个策略"""
        db = get_session()
        try:
            r = db.query(AlphaLabStrategy).filter(AlphaLabStrategy.id == strategy_id).first()
            if not r:
                return None
            return {
                "id": r.id,
                "session_id": r.session_id,
                "iteration": r.iteration,
                "code": r.code,
                "ai_reasoning": r.ai_reasoning,
                "train_metrics": json.loads(r.train_metrics) if r.train_metrics else {},
                "val_metrics": json.loads(r.val_metrics) if r.val_metrics else {},
                "overfit_score": r.overfit_score,
                "composite_score": r.composite_score,
                "walk_forward_results": json.loads(r.walk_forward_results) if r.walk_forward_results else None,
                "status": r.status,
                "error_message": r.error_message,
                "deployed": bool(r.deployed),
                "created_at": str(r.created_at) if r.created_at else None,
            }
        finally:
            db.close()

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其关联的策略和日志"""
        db = get_session()
        try:
            session = db.query(AlphaLabSession).filter(AlphaLabSession.id == session_id).first()
            if not session:
                return False
            db.query(AlphaLabLog).filter(AlphaLabLog.session_id == session_id).delete()
            db.query(AlphaLabStrategy).filter(AlphaLabStrategy.session_id == session_id).delete()
            db.delete(session)
            db.commit()
            logger.info(f"已删除 Alpha Lab 会话: {session_id}")
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"删除会话失败: {e}")
            raise
        finally:
            db.close()
