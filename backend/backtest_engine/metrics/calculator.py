"""
性能指标计算器
"""
import pandas as pd
import numpy as np
from typing import List, Dict

from loguru import logger


class MetricsCalculator:
    """性能指标计算器"""

    @staticmethod
    def calculate_returns(equity_curve: List[dict]) -> pd.Series:
        """
        计算收益率序列

        Args:
            equity_curve: 权益曲线

        Returns:
            收益率序列
        """
        df = pd.DataFrame(equity_curve)
        df["returns"] = df["total_value"].pct_change()
        return df["returns"]

    @staticmethod
    def total_return(equity_curve: List[dict]) -> float:
        """总收益率"""
        if not equity_curve:
            return 0.0

        initial_value = equity_curve[0]["total_value"]
        final_value = equity_curve[-1]["total_value"]

        return ((final_value - initial_value) / initial_value) * 100

    @staticmethod
    def annualized_return(equity_curve: List[dict], trading_days: int = 252) -> float:
        """
        年化收益率

        Args:
            equity_curve: 权益曲线
            trading_days: 每年交易日数，默认 252
        """
        if not equity_curve or len(equity_curve) < 2:
            return 0.0

        total_ret = MetricsCalculator.total_return(equity_curve) / 100
        n_days = len(equity_curve)
        n_years = n_days / trading_days

        if n_years <= 0:
            return 0.0

        return (((1 + total_ret) ** (1 / n_years)) - 1) * 100

    @staticmethod
    def volatility(equity_curve: List[dict], trading_days: int = 252) -> float:
        """
        波动率（年化）

        Args:
            equity_curve: 权益曲线
            trading_days: 每年交易日数
        """
        returns = MetricsCalculator.calculate_returns(equity_curve)
        if len(returns) < 2:
            return 0.0

        return returns.std() * np.sqrt(trading_days) * 100

    @staticmethod
    def sharpe_ratio(equity_curve: List[dict], risk_free_rate: float = 0.03) -> float:
        """
        夏普比率

        Args:
            equity_curve: 权益曲线
            risk_free_rate: 无风险利率，默认 3%
        """
        ann_return = MetricsCalculator.annualized_return(equity_curve) / 100
        vol = MetricsCalculator.volatility(equity_curve) / 100

        if vol == 0:
            return 0.0

        return (ann_return - risk_free_rate) / vol

    @staticmethod
    def max_drawdown(equity_curve: List[dict]) -> Dict[str, float]:
        """
        最大回撤

        Returns:
            包含最大回撤、开始时间、结束时间的字典
        """
        if not equity_curve:
            return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0}

        df = pd.DataFrame(equity_curve)
        cumulative = df["total_value"]

        # 计算累计最高值
        running_max = cumulative.expanding().max()

        # 计算回撤
        drawdown = cumulative - running_max
        drawdown_pct = (drawdown / running_max) * 100

        # 找到最大回撤
        max_dd_idx = drawdown_pct.idxmin()
        max_dd = drawdown.iloc[max_dd_idx]
        max_dd_pct = drawdown_pct.iloc[max_dd_idx]

        # 找到最大回撤的起始点
        try:
            if max_dd_idx > 0:
                max_dd_start_idx = cumulative[:max_dd_idx].idxmax()
                return {
                    "max_drawdown": abs(max_dd),
                    "max_drawdown_pct": abs(max_dd_pct),
                    "start_date": df.iloc[max_dd_start_idx]["timestamp"],
                    "end_date": df.iloc[max_dd_idx]["timestamp"]
                }
        except (IndexError, KeyError, ValueError) as exc:
            # 起始点定位失败（空切片 / idxmax 无解），退化为不带日期的结果
            logger.warning(f"最大回撤起始点定位失败，退化为无日期结果: {exc}")

        return {
            "max_drawdown": abs(max_dd) if not pd.isna(max_dd) else 0.0,
            "max_drawdown_pct": abs(max_dd_pct) if not pd.isna(max_dd_pct) else 0.0
        }

    @staticmethod
    def _realized_pnls(trades: List[dict]) -> List[float]:
        """FIFO 配对每笔卖出，返回每笔卖出的净盈亏（已扣买入按比例分摊的佣金 + 卖出佣金）。

        trades 中买卖 quantity 均为正数（见 Portfolio.trades 记录口径）。
        取代旧的"buy_trades[i] 对 sell_trades[i] 下标配对 + 忽略佣金"错误口径。
        """
        from collections import deque
        lots = deque()  # 每个买入批次: [剩余数量, 买入价, 每股买入佣金]
        pnls: List[float] = []
        for t in trades:
            qty = t.get("quantity", 0)
            price = t.get("price", 0.0)
            comm = t.get("commission", 0.0)
            if qty <= 0:
                continue
            if t["action"] == "BUY":
                lots.append([qty, price, comm / qty])
            elif t["action"] == "SELL":
                remaining = qty
                sell_comm_per_share = comm / qty
                pnl = 0.0
                while remaining > 0 and lots:
                    lot = lots[0]
                    take = min(remaining, lot[0])
                    # 净盈亏 = (卖价-买价)*take - 买入佣金分摊 - 卖出佣金分摊
                    pnl += (price - lot[1]) * take - lot[2] * take - sell_comm_per_share * take
                    lot[0] -= take
                    remaining -= take
                    if lot[0] == 0:
                        lots.popleft()
                pnls.append(pnl)
        return pnls

    @staticmethod
    def win_rate(trades: List[dict]) -> float:
        """胜率 = 盈利的完整买卖回合数 / 总回合数（FIFO 净盈亏口径，已计手续费）"""
        pnls = MetricsCalculator._realized_pnls(trades)
        if not pnls:
            return 0.0
        wins = sum(1 for p in pnls if p > 0)
        return (wins / len(pnls)) * 100

    @staticmethod
    def win_loss_counts(trades: List[dict]) -> Dict[str, int]:
        """胜负场数（与 win_rate 同口径：FIFO 配对 + 净盈亏，已计手续费；持平计入亏损侧）。"""
        pnls = MetricsCalculator._realized_pnls(trades)
        winning = sum(1 for p in pnls if p > 0)
        return {"winning_trades": winning, "losing_trades": len(pnls) - winning}

    @staticmethod
    def profit_factor(trades: List[dict]) -> float:
        """盈亏比 = 总盈利 / 总亏损（FIFO 净盈亏口径，已计手续费）"""
        pnls = MetricsCalculator._realized_pnls(trades)
        if not pnls:
            return 0.0
        total_profit = sum(p for p in pnls if p > 0)
        total_loss = sum(-p for p in pnls if p < 0)
        if total_loss == 0:
            return float('inf') if total_profit > 0 else 0.0
        return total_profit / total_loss

    @staticmethod
    def calculate_all(portfolio) -> Dict:
        """
        计算所有指标

        Args:
            portfolio: 投资组合

        Returns:
            所有指标的字典
        """
        equity_curve = portfolio.equity_curve
        trades = portfolio.trades

        metrics = {
            # 收益指标
            "total_return": MetricsCalculator.total_return(equity_curve),
            "annualized_return": MetricsCalculator.annualized_return(equity_curve),

            # 风险指标
            "volatility": MetricsCalculator.volatility(equity_curve),
            "sharpe_ratio": MetricsCalculator.sharpe_ratio(equity_curve),
            "max_drawdown": MetricsCalculator.max_drawdown(equity_curve),

            # 交易指标
            "win_rate": MetricsCalculator.win_rate(trades),
            "profit_factor": MetricsCalculator.profit_factor(trades),
            "num_trades": len(trades),
            **MetricsCalculator.win_loss_counts(trades),

            # 组合指标
            "initial_capital": portfolio.initial_capital,
            "final_value": portfolio.total_value,
            "total_commission": sum(t.get("commission", 0) for t in trades)
        }

        return metrics
