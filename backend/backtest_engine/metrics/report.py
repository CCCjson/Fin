"""
回测报告生成器
"""
from typing import Dict
import pandas as pd
from loguru import logger


class ReportGenerator:
    """回测报告生成器"""

    @staticmethod
    def generate_text_report(metrics: Dict, portfolio) -> str:
        """
        生成文本格式报告

        Args:
            metrics: 性能指标
            portfolio: 投资组合

        Returns:
            文本报告
        """
        lines = []
        lines.append("=" * 80)
        lines.append("回测报告")
        lines.append("=" * 80)
        lines.append("")

        # 基本信息
        lines.append("【基本信息】")
        lines.append(f"  初始资金: {metrics['initial_capital']:,.2f}")
        lines.append(f"  最终价值: {metrics['final_value']:,.2f}")
        lines.append(f"  交易次数: {metrics['num_trades']}")
        lines.append(f"  手续费: {metrics['total_commission']:.2f}")
        lines.append("")

        # 收益指标
        lines.append("【收益指标】")
        lines.append(f"  总收益率: {metrics['total_return']:.2f}%")
        lines.append(f"  年化收益率: {metrics['annualized_return']:.2f}%")
        lines.append("")

        # 风险指标
        lines.append("【风险指标】")
        lines.append(f"  波动率: {metrics['volatility']:.2f}%")
        lines.append(f"  夏普比率: {metrics['sharpe_ratio']:.2f}")

        max_dd = metrics['max_drawdown']
        lines.append(f"  最大回撤: {max_dd['max_drawdown']:.2f} ({max_dd['max_drawdown_pct']:.2f}%)")

        if 'start_date' in max_dd:
            lines.append(f"    回撤期间: {max_dd['start_date']} ~ {max_dd['end_date']}")
        lines.append("")

        # 交易指标
        lines.append("【交易指标】")
        lines.append(f"  胜率: {metrics['win_rate']:.2f}%")

        pf = metrics['profit_factor']
        pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
        lines.append(f"  盈亏比: {pf_str}")
        lines.append("")

        # 当前持仓
        if portfolio.positions:
            lines.append("【当前持仓】")
            for symbol, position in portfolio.positions.items():
                lines.append(f"  {symbol}:")
                lines.append(f"    数量: {position.quantity}")
                lines.append(f"    成本: {position.avg_price:.2f}")
                lines.append(f"    现价: {position.current_price:.2f}")
                lines.append(f"    盈亏: {position.unrealized_pnl:.2f} ({position.unrealized_pnl_pct:.2f}%)")
            lines.append("")

        lines.append("=" * 80)

        return "\n".join(lines)

    @staticmethod
    def print_report(metrics: Dict, portfolio):
        """打印报告"""
        report = ReportGenerator.generate_text_report(metrics, portfolio)
        print(report)

    @staticmethod
    def log_report(metrics: Dict, portfolio):
        """使用 logger 输出报告"""
        report = ReportGenerator.generate_text_report(metrics, portfolio)
        for line in report.split("\n"):
            logger.info(line)

    @staticmethod
    def export_trades_csv(trades: list, filename: str):
        """导出交易记录到 CSV"""
        if not trades:
            logger.warning("无交易记录")
            return

        df = pd.DataFrame(trades)
        df.to_csv(filename, index=False)
        logger.info(f"交易记录已导出到: {filename}")

    @staticmethod
    def export_equity_curve_csv(equity_curve: list, filename: str):
        """导出权益曲线到 CSV"""
        if not equity_curve:
            logger.warning("无权益曲线数据")
            return

        df = pd.DataFrame(equity_curve)
        df.to_csv(filename, index=False)
        logger.info(f"权益曲线已导出到: {filename}")
