"""
性能监控器
"""
from typing import Dict, List
from datetime import datetime
import numpy as np
from loguru import logger


class PerformanceMonitor:
    """性能监控器 - 实时监控账户绩效"""

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.snapshots: List[Dict] = []
        self.daily_returns: List[float] = []

    def take_snapshot(
        self,
        timestamp: datetime,
        cash: float,
        market_value: float,
        total_value: float,
        positions: Dict = None
    ):
        """记录账户快照"""
        snapshot = {
            "timestamp": timestamp,
            "cash": cash,
            "market_value": market_value,
            "total_value": total_value,
            "return": total_value - self.initial_capital,
            "return_pct": ((total_value - self.initial_capital) / self.initial_capital) * 100,
            "positions": positions or {}
        }

        self.snapshots.append(snapshot)

        # 计算日收益率
        if len(self.snapshots) > 1:
            prev_value = self.snapshots[-2]["total_value"]
            daily_return = (total_value - prev_value) / prev_value
            self.daily_returns.append(daily_return)

    def get_current_performance(self) -> Dict:
        """获取当前绩效"""
        if not self.snapshots:
            return {
                "total_value": self.initial_capital,
                "return": 0,
                "return_pct": 0
            }

        latest = self.snapshots[-1]
        return {
            "timestamp": latest["timestamp"],
            "cash": latest["cash"],
            "market_value": latest["market_value"],
            "total_value": latest["total_value"],
            "return": latest["return"],
            "return_pct": latest["return_pct"]
        }

    def get_statistics(self) -> Dict:
        """获取统计指标"""
        if len(self.snapshots) < 2:
            return {}

        values = [s["total_value"] for s in self.snapshots]
        returns = self.daily_returns

        # 收益指标
        total_return = ((values[-1] - values[0]) / values[0]) * 100

        # 波动率
        volatility = np.std(returns) * np.sqrt(252) * 100 if returns else 0

        # 最大回撤
        max_dd = self._calculate_max_drawdown(values)

        # 夏普比率
        sharpe = self._calculate_sharpe_ratio(returns) if returns else 0

        # 胜率
        win_rate = self._calculate_win_rate(returns) if returns else 0

        return {
            "total_return_pct": total_return,
            "volatility": volatility,
            "max_drawdown": max_dd["max_drawdown"],
            "max_drawdown_pct": max_dd["max_drawdown_pct"],
            "sharpe_ratio": sharpe,
            "win_rate": win_rate,
            "num_snapshots": len(self.snapshots),
            "num_trading_days": len(returns)
        }

    def _calculate_max_drawdown(self, values: List[float]) -> Dict:
        """计算最大回撤"""
        peak = values[0]
        max_dd = 0
        max_dd_pct = 0

        for value in values:
            if value > peak:
                peak = value

            drawdown = peak - value
            drawdown_pct = (drawdown / peak) * 100

            if drawdown > max_dd:
                max_dd = drawdown
                max_dd_pct = drawdown_pct

        return {
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd_pct
        }

    def _calculate_sharpe_ratio(self, returns: List[float], risk_free_rate: float = 0.03) -> float:
        """计算夏普比率"""
        if not returns or len(returns) < 2:
            return 0

        mean_return = np.mean(returns) * 252  # 年化
        std_return = np.std(returns) * np.sqrt(252)  # 年化

        if std_return == 0:
            return 0

        return (mean_return - risk_free_rate) / std_return

    def _calculate_win_rate(self, returns: List[float]) -> float:
        """计算胜率"""
        if not returns:
            return 0

        wins = len([r for r in returns if r > 0])
        return (wins / len(returns)) * 100

    def get_equity_curve(self) -> List[Dict]:
        """获取权益曲线"""
        return [
            {
                "timestamp": s["timestamp"],
                "total_value": s["total_value"],
                "return_pct": s["return_pct"]
            }
            for s in self.snapshots
        ]

    def get_drawdown_curve(self) -> List[Dict]:
        """获取回撤曲线"""
        if not self.snapshots:
            return []

        values = [s["total_value"] for s in self.snapshots]
        peak = values[0]
        drawdowns = []

        for i, value in enumerate(values):
            if value > peak:
                peak = value

            drawdown = peak - value
            drawdown_pct = (drawdown / peak) * 100

            drawdowns.append({
                "timestamp": self.snapshots[i]["timestamp"],
                "drawdown": drawdown,
                "drawdown_pct": drawdown_pct
            })

        return drawdowns

    def print_summary(self):
        """打印绩效摘要"""
        current = self.get_current_performance()
        stats = self.get_statistics()

        logger.info("\n" + "=" * 80)
        logger.info("绩效摘要")
        logger.info("=" * 80)

        logger.info(f"\n初始资金: {self.initial_capital:,.2f}")
        logger.info(f"当前总资产: {current.get('total_value', 0):,.2f}")
        logger.info(f"总收益: {current.get('return', 0):,.2f} ({current.get('return_pct', 0):.2f}%)")

        if stats:
            logger.info(f"\n波动率: {stats['volatility']:.2f}%")
            logger.info(f"夏普比率: {stats['sharpe_ratio']:.2f}")
            logger.info(f"最大回撤: {stats['max_drawdown']:.2f} ({stats['max_drawdown_pct']:.2f}%)")
            logger.info(f"胜率: {stats['win_rate']:.2f}%")

        logger.info("=" * 80)

    def __repr__(self):
        return f"PerformanceMonitor(快照数={len(self.snapshots)})"
