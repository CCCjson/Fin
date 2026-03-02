"""
迭代优化 Prompt
"""
from typing import Dict, Optional, List


def build_iteration_prompt(
    iteration: int,
    prev_train_metrics: Dict,
    prev_val_metrics: Dict,
    prev_overfit_score: float,
    prev_overfit_warnings: List[str],
    prev_composite_score: float,
    best_iteration: Optional[int],
    best_val_sharpe: Optional[float],
    best_composite_score: Optional[float],
    phase: str,
) -> str:
    """构建迭代优化的 user prompt"""

    feedback = f"""## 第 {iteration - 1} 轮回测结果

### 训练集表现
{_format_metrics(prev_train_metrics)}

### 验证集表现
{_format_metrics(prev_val_metrics)}

### 过拟合检测
- 过拟合分数: {prev_overfit_score:.3f} {'⚠️ 偏高' if prev_overfit_score > 0.3 else '✅ 正常'}
"""

    if prev_overfit_warnings:
        for w in prev_overfit_warnings:
            feedback += f"- {w}\n"

    feedback += f"\n### 综合评分: {prev_composite_score:.4f}\n"

    if best_iteration is not None and best_iteration != iteration - 1:
        feedback += f"""
### 历史最佳（第 {best_iteration} 轮）
- 验证集 Sharpe: {best_val_sharpe:.3f}
- 综合评分: {best_composite_score:.4f}
"""

    if phase == "explore":
        feedback += """
请基于以上结果，生成一个**改进版**策略。你可以：
- 尝试完全不同的策略思路（趋势、反转、突破、多因子）
- 调整入场/出场条件
- 改进止损逻辑
- 优化仓位管理

注意：如果过拟合分数偏高，请简化策略（减少参数、用更长周期指标）。

直接输出一个完整的 Python 代码块。
"""
    else:
        feedback += """
当前进入 **精炼期**。请基于历史最佳策略进行**微调优化**：
- 小幅调整参数（不要大改逻辑）
- 增加过滤条件减少假信号
- 优化止损/止盈比例
- 考虑加入趋势过滤器或波动率自适应

目标：提高验证集表现，同时降低过拟合分数。

直接输出一个完整的 Python 代码块。
"""

    return feedback


def _safe_float(value, default: float = 0.0) -> float:
    """安全转换为 float，非数值类型返回默认值"""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_metrics(metrics: Dict) -> str:
    """格式化指标为 Markdown 表格"""
    if not metrics:
        return "（无数据）"

    lines = []
    sharpe = _safe_float(metrics.get("sharpe_ratio", 0))
    ann_ret = _safe_float(metrics.get("annualized_return", 0))
    total_ret = _safe_float(metrics.get("total_return", 0))
    win = _safe_float(metrics.get("win_rate", 0))
    trades = metrics.get("num_trades", 0)
    pf = _safe_float(metrics.get("profit_factor", 0))

    dd = metrics.get("max_drawdown", {})
    if isinstance(dd, dict):
        dd_pct = _safe_float(dd.get("max_drawdown_pct", 0))
    else:
        dd_pct = _safe_float(dd)

    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总收益 | {total_ret:.2f}% |")
    lines.append(f"| 年化收益 | {ann_ret:.2f}% |")
    lines.append(f"| 夏普比率 | {sharpe:.3f} |")
    lines.append(f"| 最大回撤 | {dd_pct:.2f}% |")
    lines.append(f"| 胜率 | {win:.1f}% |")
    lines.append(f"| 盈亏比 | {pf:.2f} |")
    lines.append(f"| 交易次数 | {trades} |")

    return "\n".join(lines)
