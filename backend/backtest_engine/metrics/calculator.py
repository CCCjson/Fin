"""
性能指标计算器
"""
import pandas as pd
import numpy as np
from typing import List, Dict


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
        except:
            pass

        return {
            "max_drawdown": abs(max_dd) if not pd.isna(max_dd) else 0.0,
            "max_drawdown_pct": abs(max_dd_pct) if not pd.isna(max_dd_pct) else 0.0
        }

    @staticmethod
    def win_rate(trades: List[dict]) -> float:
        """
        胜率

        Args:
            trades: 交易记录
        """
        if not trades:
            return 0.0

        # 只统计完整的买卖对
        buy_trades = [t for t in trades if t["action"] == "BUY"]
        sell_trades = [t for t in trades if t["action"] == "SELL"]

        if len(sell_trades) == 0:
            return 0.0

        wins = 0
        for i, sell in enumerate(sell_trades):
            if i < len(buy_trades):
                buy = buy_trades[i]
                if sell["price"] > buy["price"]:
                    wins += 1

        return (wins / len(sell_trades)) * 100

    @staticmethod
    def profit_factor(trades: List[dict]) -> float:
        """
        盈亏比 = 总盈利 / 总亏损

        Args:
            trades: 交易记录
        """
        if not trades:
            return 0.0

        buy_trades = [t for t in trades if t["action"] == "BUY"]
        sell_trades = [t for t in trades if t["action"] == "SELL"]

        if len(sell_trades) == 0:
            return 0.0

        total_profit = 0.0
        total_loss = 0.0

        for i, sell in enumerate(sell_trades):
            if i < len(buy_trades):
                buy = buy_trades[i]
                pnl = (sell["price"] - buy["price"]) * sell["quantity"]

                if pnl > 0:
                    total_profit += pnl
                else:
                    total_loss += abs(pnl)

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

            # 组合指标
            "initial_capital": portfolio.initial_capital,
            "final_value": portfolio.total_value,
            "total_commission": sum(t.get("commission", 0) for t in trades)
        }

        return metrics
