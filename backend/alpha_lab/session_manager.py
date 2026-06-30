"""
会话管理器 — 管理 Alpha Lab 迭代会话的运行时状态
"""
import uuid
import time
import threading
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class IterationRecord:
    """单轮迭代记录"""
    iteration: int
    code: str
    train_metrics: Dict
    val_metrics: Dict
    overfit_score: float
    composite_score: float
    model: str
    tokens: int


@dataclass
class SessionState:
    """会话运行时状态"""
    id: str
    target_symbols: List[str]
    optimization_goal: str
    data_range: Tuple[str, str]
    max_iterations: int
    initial_capital: float
    constraints: Dict

    # 运行状态
    status: str = "running"
    current_iteration: int = 0
    phase: str = "explore"

    # AI 对话上下文
    messages: List[Dict] = field(default_factory=list)

    # 迭代历史
    iterations: List[IterationRecord] = field(default_factory=list)
    best_iteration: Optional[int] = None
    best_val_sharpe: float = -999.0
    best_composite_score: float = -999.0

    # 成本
    total_tokens: int = 0
    total_cost: float = 0.0

    created_at: float = field(default_factory=time.time)


class SessionManager:
    """会话管理器"""

    # 模型费率（每 1K token 平均，(输入价+输出价)/2 / 1000）
    COST_RATES = {
        # GPT-5 系列（2026 在售）
        "gpt-5.5": 0.0175,         # $5/1M in + $30/1M out
        "gpt-5.4": 0.00875,        # $2.5/1M in + $15/1M out
        "gpt-5.4-mini": 0.001,     # 估算（官方未公布精确价，介于 nano 与 5.4 之间）
        "gpt-5.4-nano": 0.000725,  # $0.20/1M in + $1.25/1M out
        # 旧模型（保留兼容历史记录）
        "gpt-4o-mini": 0.00038,    # ~$0.15/1M in + $0.60/1M out 平均
        "gpt-4o": 0.00625,         # ~$2.5/1M in + $10/1M out 平均
    }

    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def create(
        self,
        target_symbols: List[str],
        optimization_goal: str,
        data_range: Tuple[str, str],
        max_iterations: int = 15,
        initial_capital: float = 1000000.0,
        constraints: Optional[Dict] = None,
    ) -> SessionState:
        session_id = f"alab_{uuid.uuid4().hex[:12]}"
        session = SessionState(
            id=session_id,
            target_symbols=target_symbols,
            optimization_goal=optimization_goal,
            data_range=data_range,
            max_iterations=max_iterations,
            initial_capital=initial_capital,
            constraints=constraints or {},
        )
        with self._lock:
            self._sessions[session_id] = session
        logger.info(f"Alpha Lab 会话创建: {session_id}")
        return session

    def get(self, session_id: str) -> SessionState:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"会话不存在: {session_id}")
        return session

    def list_sessions(self) -> List[Dict]:
        """列出所有会话摘要"""
        result = []
        for s in self._sessions.values():
            result.append({
                "id": s.id,
                "target_symbols": s.target_symbols,
                "optimization_goal": s.optimization_goal,
                "status": s.status,
                "total_iterations": s.current_iteration,
                "best_sharpe": s.best_val_sharpe if s.best_val_sharpe > -999 else None,
                "best_composite_score": s.best_composite_score if s.best_composite_score > -999 else None,
                "cost_usd": round(s.total_cost, 4),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.created_at)),
            })
        return sorted(result, key=lambda x: x["created_at"], reverse=True)

    def update_best(self, session: SessionState, iteration: int, val_sharpe: float, composite: float):
        """更新最佳记录"""
        if composite > session.best_composite_score:
            session.best_val_sharpe = val_sharpe
            session.best_composite_score = composite
            session.best_iteration = iteration
            logger.info(f"[{session.id}] 新最佳: iter={iteration}, sharpe={val_sharpe:.3f}, score={composite:.4f}")

    def add_cost(self, session: SessionState, tokens: int, model: str):
        """追加成本（本地模型费率为 0）"""
        session.total_tokens += tokens
        rate = self.COST_RATES.get(model, 0.0)  # 未知模型（本地模型）费率为 0
        session.total_cost += tokens * rate / 1000

    def add_iteration(self, session: SessionState, record: IterationRecord):
        """记录一轮迭代"""
        session.iterations.append(record)
        session.current_iteration = record.iteration
