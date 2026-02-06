"""
回测引擎主类
"""
import pandas as pd
from typing import Optional
from datetime import datetime
from loguru import logger

from .strategies import BaseStrategy, StrategyContext
from .portfolio import Portfolio, OrderType
from .metrics import MetricsCalculator, ReportGenerator


class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        initial_capital: float = 100000.0,
        commission_rate: float = 0.0003
    ):
        """
        初始化回测引擎

        Args:
            initial_capital: 初始资金
            commission_rate: 手续费率
        """
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.portfolio: Optional[Portfolio] = None
        self.strategy: Optional[BaseStrategy] = None
        self.symbol: Optional[str] = None
        self.data: Optional[pd.DataFrame] = None
        self.results: Optional[dict] = None

    def run(
        self,
        symbol: str,
        data: pd.DataFrame,
        strategy: BaseStrategy,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ):
        """
        运行回测

        Args:
            symbol: 股票代码
            data: 历史数据（包含技术指标）
            strategy: 交易策略
            start_date: 开始日期
            end_date: 结束日期
        """
        logger.info("\n" + "=" * 80)
        logger.info(f"开始回测: {symbol}")
        logger.info(f"策略: {strategy}")
        logger.info(f"初始资金: {self.initial_capital:,.2f}")
        logger.info("=" * 80 + "\n")

        # 初始化
        self.symbol = symbol
        self.data = data.copy()
        self.strategy = strategy
        self.portfolio = Portfolio(
            initial_capital=self.initial_capital,
            commission_rate=self.commission_rate
        )

        # 过滤日期范围
        if start_date:
            self.data = self.data[self.data.index >= start_date]
        if end_date:
            self.data = self.data[self.data.index <= end_date]

        if self.data.empty:
            logger.error("数据为空")
            return

        logger.info(f"回测周期: {self.data.index[0]} ~ {self.data.index[-1]}")
        logger.info(f"数据条数: {len(self.data)}\n")

        # 策略初始化
        strategy.on_start()

        # 逐日回测
        for i in range(len(self.data)):
            current_date = self.data.index[i]
            current_price = self.data.iloc[i]["close"]

            # 更新持仓价格
            self.portfolio.update_prices({symbol: current_price})

            # 构建策略上下文
            context = StrategyContext(
                symbol=symbol,
                current_time=current_date,
                current_price=current_price,
                data=self.data.iloc[:i+1],  # 历史数据（包含当前）
                cash=self.portfolio.cash,
                position_quantity=self.portfolio.get_position(symbol).quantity if self.portfolio.has_position(symbol) else 0,
                position_avg_price=self.portfolio.get_position(symbol).avg_price if self.portfolio.has_position(symbol) else 0.0,
                total_value=self.portfolio.total_value
            )

            # 生成信号
            orders = strategy.generate_signals(context)

            # 处理订单
            for order in orders:
                success = self.portfolio.process_order(order, current_price)
                if success:
                    action = "买入" if order.is_buy else "卖出"
                    # 安全地格式化日期
                    date_str = current_date.strftime('%Y-%m-%d') if hasattr(current_date, 'strftime') else str(current_date)
                    logger.info(
                        f"[{date_str}] "
                        f"{action} {abs(order.quantity)} 股 @ {order.filled_price:.2f}, "
                        f"现金: {self.portfolio.cash:.2f}, "
                        f"总资产: {self.portfolio.total_value:.2f}"
                    )

            # 记录权益曲线
            self.portfolio.record_equity(current_date)

        # 策略结束
        strategy.on_finish()

        # 计算性能指标
        logger.info("\n" + "=" * 80)
        logger.info("回测完成，计算性能指标...")
        logger.info("=" * 80 + "\n")

        metrics = MetricsCalculator.calculate_all(self.portfolio)

        # 保存结果
        self.results = {
            "symbol": symbol,
            "strategy": strategy.name,
            "metrics": metrics,
            "portfolio": self.portfolio,
            "equity_curve": self.portfolio.equity_curve,
            "trades": self.portfolio.trades,
            "orders": self.portfolio.orders
        }

        # 生成报告
        ReportGenerator.log_report(metrics, self.portfolio)

        return self.results

    def get_results(self) -> Optional[dict]:
        """获取回测结果"""
        return self.results

    def export_results(self, output_dir: str = "./backtest_results"):
        """
        导出回测结果

        Args:
            output_dir: 输出目录
        """
        if not self.results:
            logger.warning("无回测结果")
            return

        import os
        os.makedirs(output_dir, exist_ok=True)

        # 导出交易记录
        trades_file = f"{output_dir}/trades_{self.symbol}_{self.strategy.name}.csv"
        ReportGenerator.export_trades_csv(self.results["trades"], trades_file)

        # 导出权益曲线
        equity_file = f"{output_dir}/equity_{self.symbol}_{self.strategy.name}.csv"
        ReportGenerator.export_equity_curve_csv(self.results["equity_curve"], equity_file)

        logger.success(f"回测结果已导出到: {output_dir}")
