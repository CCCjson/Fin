"""
回测任务执行器 - 负责执行回测任务
"""
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger

from backtest_engine.engine import BacktestEngine
from backtest_engine.strategies import (
    MACrossStrategy,
    SignalStrategy,
    MACDStrategy,
    KDJStrategy,
    RSIStrategy
)
from data_engine import DataEngine
from data_engine.storage.history_repository import HistoryRepository
from strategy.indicators import TechnicalIndicators


class BacktestExecutor:
    """回测任务执行器"""

    def __init__(self):
        self.data_engine = DataEngine()
        self.indicators = TechnicalIndicators()

    def execute_task(self, task_id: str):
        """
        执行回测任务

        Args:
            task_id: 任务ID
        """
        repo = HistoryRepository()

        try:
            # 获取任务信息
            tasks = repo.get_backtest_tasks(limit=1000)
            task = next((t for t in tasks if t.task_id == task_id), None)

            if not task:
                logger.error(f"任务不存在: {task_id}")
                return

            logger.info(f"开始执行回测任务: {task_id}")
            logger.info(f"任务名称: {task.name}")
            logger.info(f"策略类型: {task.strategy_type}")

            # 更新任务状态为 running
            repo.update_backtest_status(task_id, "running")

            # 解析任务配置
            import json
            symbols = json.loads(task.symbols) if task.symbols else []
            strategy_params = json.loads(task.strategy_params) if task.strategy_params else {}

            logger.info(f"股票列表: {symbols}")
            logger.info(f"日期范围: {task.start_date} ~ {task.end_date}")

            # 执行回测
            all_results = []
            all_trades = []
            all_daily_records = []

            for symbol in symbols:
                try:
                    logger.info(f"\n{'='*60}")
                    logger.info(f"回测股票: {symbol}")
                    logger.info(f"{'='*60}")

                    # 获取数据
                    df = self.data_engine.get_daily_data(
                        symbol=symbol,
                        start_date=str(task.start_date),
                        end_date=str(task.end_date)
                    )

                    if df.empty:
                        logger.warning(f"股票 {symbol} 无数据，跳过")
                        continue

                    # 计算技术指标
                    df = self.indicators.calculate_all_indicators(df)

                    # 创建策略
                    strategy = self._create_strategy(task.strategy_type, strategy_params)
                    if not strategy:
                        logger.error(f"不支持的策略类型: {task.strategy_type}")
                        continue

                    # 创建回测引擎
                    engine = BacktestEngine(initial_capital=task.initial_capital)

                    # 运行回测
                    result = engine.run(
                        symbol=symbol,
                        data=df,
                        strategy=strategy
                    )

                    if result:
                        all_results.append(result)

                        # 收集交易记录
                        for trade in result['portfolio'].trades:
                            all_trades.append({
                                'date': str(trade.get('timestamp', '')),
                                'symbol': symbol,
                                'action': trade.get('action', ''),
                                'quantity': trade.get('quantity', 0),
                                'price': trade.get('price', 0),
                                'commission': trade.get('commission', 0),
                                'amount': trade.get('quantity', 0) * trade.get('price', 0)
                            })

                        # 收集每日记录
                        for snapshot in result['portfolio'].equity_curve:
                            # equity_curve 中的字段是 'timestamp'
                            timestamp = snapshot['timestamp']

                            # pandas Timestamp 转字符串
                            try:
                                date_str = timestamp.strftime('%Y-%m-%d')
                            except (AttributeError, ValueError):
                                # 已经是字符串或非 Timestamp 类型，退化为切片取日期
                                date_str = str(timestamp)[:10]

                            all_daily_records.append({
                                'date': date_str,
                                'symbol': symbol,
                                'total_value': snapshot['total_value'],
                                'cash': snapshot['cash'],
                                'market_value': snapshot['market_value'],
                                'daily_return': snapshot.get('return_pct', 0)
                            })

                except Exception as e:
                    logger.error(f"股票 {symbol} 回测失败: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            # 汇总结果
            if not all_results:
                logger.error("所有股票回测失败")
                repo.update_backtest_status(task_id, "failed", "所有股票回测失败")
                repo.close()
                return

            aggregated_metrics = self._aggregate_metrics(all_results, task.initial_capital)

            # 保存回测结果
            repo.save_backtest_result(
                task_id=task_id,
                metrics=aggregated_metrics,
                daily_records=all_daily_records,
                trade_records=all_trades
            )

            # 更新任务状态为 completed
            repo.update_backtest_status(task_id, "completed")

            logger.success(f"回测任务完成: {task_id}")
            logger.info(f"总收益率: {aggregated_metrics['total_return_pct']:.2f}%")
            logger.info(f"夏普比率: {aggregated_metrics['sharpe_ratio']:.2f}")
            logger.info(f"最大回撤: {aggregated_metrics['max_drawdown_pct']:.2f}%")

        except Exception as e:
            logger.error(f"回测任务执行失败: {e}")
            import traceback
            traceback.print_exc()
            repo.update_backtest_status(task_id, "failed", str(e))

        finally:
            repo.close()

    def _create_strategy(self, strategy_type: str, params: dict):
        """创建策略实例"""
        strategy_type = strategy_type.upper()

        if strategy_type == "MA_CROSS":
            return MACrossStrategy(
                fast_period=params.get('fast_period', 5),
                slow_period=params.get('slow_period', 20)
            )
        elif strategy_type == "SIGNAL":
            return SignalStrategy(
                signal_types=params.get('signal_types')
            )
        elif strategy_type == "MACD":
            return MACDStrategy(
                position_size=params.get('position_size', 0.95)
            )
        elif strategy_type == "KDJ":
            return KDJStrategy(
                oversold=params.get('oversold', 20.0),
                overbought=params.get('overbought', 80.0),
                position_size=params.get('position_size', 0.95)
            )
        elif strategy_type == "RSI":
            return RSIStrategy(
                oversold=params.get('oversold', 30.0),
                overbought=params.get('overbought', 70.0),
                position_size=params.get('position_size', 0.95)
            )
        else:
            return None

    def _aggregate_metrics(self, results: List[dict], initial_capital: float) -> Dict:
        """
        汇总多个股票的回测指标

        Args:
            results: 各股票的回测结果列表
            initial_capital: 初始资金

        Returns:
            汇总后的指标
        """
        if not results:
            return {}

        # 如果只有一个股票，直接返回其指标
        if len(results) == 1:
            metrics = results[0]['metrics']
            max_dd = metrics.get('max_drawdown', {})
            if isinstance(max_dd, dict):
                max_drawdown = max_dd.get('max_drawdown', 0)
                max_drawdown_pct = max_dd.get('max_drawdown_pct', 0)
            else:
                max_drawdown = max_dd
                max_drawdown_pct = max_dd

            return {
                'total_return': metrics.get('final_value', initial_capital) - initial_capital,
                'total_return_pct': metrics.get('total_return', 0),
                'annual_return': metrics.get('annualized_return', 0),
                'final_value': metrics.get('final_value', initial_capital),
                'max_drawdown': max_drawdown,
                'max_drawdown_pct': max_drawdown_pct,
                'volatility': metrics.get('volatility', 0),
                'sharpe_ratio': metrics.get('sharpe_ratio', 0),
                'sortino_ratio': 0,  # 当前未实现
                'total_trades': metrics.get('num_trades', 0),
                'winning_trades': 0,  # 需要从trades计算
                'losing_trades': 0,   # 需要从trades计算
                'win_rate': metrics.get('win_rate', 0),
                'profit_factor': metrics.get('profit_factor', 0)
            }

        # 多个股票：简单平均（这里可以根据需要改进为加权平均等）
        final_values = [r['metrics'].get('final_value', initial_capital) for r in results]
        avg_final_value = sum(final_values) / len(final_values)
        total_return = avg_final_value - initial_capital
        total_return_pct = (total_return / initial_capital) * 100

        total_trades = sum(r['metrics'].get('num_trades', 0) for r in results)

        # 处理 max_drawdown（可能是字典）
        max_drawdowns = []
        for r in results:
            max_dd = r['metrics'].get('max_drawdown', {})
            if isinstance(max_dd, dict):
                max_drawdowns.append(max_dd.get('max_drawdown_pct', 0))
            else:
                max_drawdowns.append(max_dd)
        avg_max_drawdown = sum(max_drawdowns) / len(max_drawdowns) if max_drawdowns else 0

        return {
            'total_return': total_return,
            'total_return_pct': total_return_pct,
            'annual_return': sum(r['metrics'].get('annualized_return', 0) for r in results) / len(results),
            'final_value': avg_final_value,
            'max_drawdown': avg_max_drawdown,
            'max_drawdown_pct': avg_max_drawdown,
            'volatility': sum(r['metrics'].get('volatility', 0) for r in results) / len(results),
            'sharpe_ratio': sum(r['metrics'].get('sharpe_ratio', 0) for r in results) / len(results),
            'sortino_ratio': 0,
            'total_trades': total_trades,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': sum(r['metrics'].get('win_rate', 0) for r in results) / len(results),
            'profit_factor': sum(r['metrics'].get('profit_factor', 0) for r in results) / len(results)
        }
