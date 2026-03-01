"""
首次生成 Prompt
"""
from typing import List, Dict, Optional


def build_first_generate_prompt(
    symbols: List[str],
    optimization_goal: str,
    data_summary: Dict,
    constraints: Optional[Dict] = None,
) -> str:
    """构建首次生成策略的 user prompt"""

    goal_desc = {
        "sharpe": "最大化夏普比率（风险调整后收益最优）",
        "return": "最大化年化收益率",
        "win_rate": "最大化胜率（同时保持合理盈亏比）",
        "drawdown": "最小化最大回撤（控制风险，同时保持正收益）",
    }

    prompt = f"""请为以下股票生成一个交易策略：

**目标股票**: {', '.join(symbols)}
**优化目标**: {goal_desc.get(optimization_goal, optimization_goal)}
**训练集**: 约 {data_summary.get('train_days', 'N/A')} 个交易日
**价格范围**: {data_summary.get('price_range', 'N/A')}
**日均成交量**: {data_summary.get('avg_volume', 'N/A')}
"""

    if constraints:
        constraint_lines = []
        if constraints.get("max_params"):
            constraint_lines.append(f"- 参数不超过 {constraints['max_params']} 个")
        if constraints.get("max_drawdown"):
            constraint_lines.append(f"- 最大回撤不超过 {constraints['max_drawdown']}%")
        if constraints.get("min_trades"):
            constraint_lines.append(f"- 交易次数不少于 {constraints['min_trades']} 次")
        if constraints.get("strategy_type"):
            constraint_lines.append(f"- 策略类型偏好: {constraints['strategy_type']}")
        if constraint_lines:
            prompt += "\n**额外约束**:\n" + "\n".join(constraint_lines) + "\n"

    prompt += """
请思考后生成策略代码，考虑以下要点：
1. 什么市场环境适合什么策略？（趋势跟踪 / 均值回归 / 突破 / 动量）
2. 如何组合多个指标过滤假信号？
3. 止损和止盈如何设置才合理？
4. 仓位管理如何平衡风险与收益？

直接输出一个完整的 Python 代码块。
"""
    return prompt
