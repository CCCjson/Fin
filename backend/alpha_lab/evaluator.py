"""
评估器 — 回测结果评估 + 反过拟合检测
"""
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class EvaluationResult:
    """单轮评估结果"""
    iteration: int
    strategy_code: str
    train_metrics: Dict
    val_metrics: Dict
    overfit_score: float
    overfit_warnings: List[str]
    composite_score: float
    execution_time: float = 0.0
    error: Optional[str] = None


class Evaluator:
    """回测结果评估器"""

    OVERFIT_WARNING_THRESHOLD = 0.3
    OVERFIT_REJECT_THRESHOLD = 0.5

    def evaluate(
        self,
        iteration: int,
        code: str,
        backtest_result: Dict,
        optimization_goal: str,
    ) -> EvaluationResult:
        """评估一轮回测结果"""
        train_metrics = backtest_result.get("train_metrics", {})
        val_metrics = backtest_result.get("val_metrics", {})

        overfit_score, warnings = self._detect_overfitting(train_metrics, val_metrics)
        composite = self._composite_score(val_metrics, optimization_goal, overfit_score)

        return EvaluationResult(
            iteration=iteration,
            strategy_code=code,
            train_metrics=train_metrics,
            val_metrics=val_metrics,
            overfit_score=overfit_score,
            overfit_warnings=warnings,
            composite_score=composite,
            execution_time=backtest_result.get("execution_time", 0),
        )

    def _detect_overfitting(
        self,
        train_metrics: Dict,
        val_metrics: Dict,
    ) -> Tuple[float, List[str]]:
        """
        过拟合检测

        overfit_score = max(0, 1 - (val_sharpe / train_sharpe))
        """
        warnings = []

        train_sharpe = self._get_sharpe(train_metrics)
        val_sharpe = self._get_sharpe(val_metrics)

        if train_sharpe <= 0:
            return 0.0, ["训练集 Sharpe ≤ 0，策略基本无效"]

        overfit_score = max(0.0, 1.0 - (val_sharpe / train_sharpe))

        if overfit_score > self.OVERFIT_REJECT_THRESHOLD:
            warnings.append(
                f"严重过拟合 (score={overfit_score:.2f})：验证集表现远差于训练集"
            )
        elif overfit_score > self.OVERFIT_WARNING_THRESHOLD:
            warnings.append(
                f"轻微过拟合 (score={overfit_score:.2f})：建议简化策略参数"
            )

        # 训练集大赚但验证集亏损
        train_ret = train_metrics.get("total_return", 0)
        val_ret = val_metrics.get("total_return", 0)
        if isinstance(train_ret, (int, float)) and isinstance(val_ret, (int, float)):
            if train_ret > 30 and val_ret < 0:
                warnings.append(
                    f"训练集收益{train_ret:.1f}%但验证集亏损{val_ret:.1f}%，过拟合风险高"
                )

        return overfit_score, warnings

    def _composite_score(
        self,
        val_metrics: Dict,
        optimization_goal: str,
        overfit_score: float,
    ) -> float:
        """综合评分 = 主指标 × (1 - 过拟合惩罚)"""
        if not val_metrics:
            return -999.0

        if optimization_goal == "sharpe":
            primary = self._get_sharpe(val_metrics)
        elif optimization_goal == "return":
            primary = val_metrics.get("annualized_return", 0)
            if not isinstance(primary, (int, float)):
                primary = 0
        elif optimization_goal == "win_rate":
            primary = val_metrics.get("win_rate", 0)
            if not isinstance(primary, (int, float)):
                primary = 0
        elif optimization_goal == "drawdown":
            dd = val_metrics.get("max_drawdown", {})
            if isinstance(dd, dict):
                dd_pct = dd.get("max_drawdown_pct", 100)
            else:
                dd_pct = abs(dd) if isinstance(dd, (int, float)) else 100
            primary = -abs(dd_pct)
        else:
            primary = self._get_sharpe(val_metrics)

        penalty = min(overfit_score * 1.5, 1.0)
        return primary * (1.0 - penalty)

    @staticmethod
    def _get_sharpe(metrics: Dict) -> float:
        """安全获取 Sharpe"""
        v = metrics.get("sharpe_ratio", 0)
        return float(v) if isinstance(v, (int, float)) else 0.0
