"""
回测引擎主类
"""
import pandas as pd
from typing import Optional
from datetime import datetime
from loguru import logger

from .strategies import BaseStrategy, StrategyContext
from .portfolio import Portfolio, OrderType, infer_market
from .metrics import MetricsCalculator, ReportGenerator


class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        initial_capital: float = 100000.0,
        commission_rate: float = 0.00025
    ):
        """
        初始化回测引擎

        Args:
            initial_capital: 初始资金
            commission_rate: 佣金率（已不再直接生效——run() 会按 symbol 后缀推断市场，
                通过 Portfolio.for_market() 套用对应市场的费率预设，A股/港股/美股各不相同；
                此参数仅为向后兼容保留）
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
        # 按 symbol 后缀推断市场（A股/港股/美股），套用对应费率预设——
        # 避免用 A 股印花税+最低佣金误伤港股/美股策略的回测结果
        self.portfolio = Portfolio.for_market(
            infer_market(symbol),
            initial_capital=self.initial_capital
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
        # 【真实成交模型】信号在 bar i 收盘产生，成交推迟到 bar i+1 开盘（next-bar-open）：
        #   杜绝"当日收盘出信号又按当日收盘价成交"的未来函数；并配合 settle_t1() 强制 A 股 T+1。
        #   （与 C++ 引擎 backtest_cpp 保持同构语义）
        pending = []  # 上一 bar 产生、待本 bar 开盘成交的订单
        has_open = "open" in self.data.columns

        for i in range(len(self.data)):
            current_date = self.data.index[i]
            current_price = self.data.iloc[i]["close"]
            # 成交价 = 今日开盘价（无 open 列则退化为收盘价，保证兼容）
            fill_price = self.data.iloc[i]["open"] if has_open else current_price

            # T+1 结算：昨日及更早买入的持仓，在今日开盘解冻为可卖
            self.portfolio.settle_t1()

            # 先按【今日开盘价】执行上一 bar 挂起的订单
            for order in pending:
                success = self.portfolio.process_order(order, fill_price)
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
            pending = []

            # 用今日收盘价更新持仓市值
            self.portfolio.update_prices({symbol: current_price})

            # 构建策略上下文（策略看到截至今日收盘的数据）
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

            # 生成信号 → 挂起到下一 bar 开盘成交（本 bar 不成交）
            orders = strategy.generate_signals(context)
            pending.extend(orders)

            # 记录权益曲线
            self.portfolio.record_equity(current_date)

        # 回测最后一 bar 的挂单没有"次日开盘"可成交 → 丢弃（现实中同样无法执行）

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
