"""
信号生成器
"""
from typing import List, Optional
import pandas as pd
from loguru import logger

from data_engine.storage.history_repository import HistoryRepository
from data_engine.engine import DataEngine
from .indicators import TechnicalIndicators
from .strategies import (
    MACrossStrategy,
    MACDStrategy,
    KDJStrategy,
    RSIStrategy
)
from .base_strategy import Signal


class SignalGenerator:
    """信号生成器"""

    def __init__(self):
        self.repo = HistoryRepository()
        self.engine = DataEngine()
        self.indicators = TechnicalIndicators()

        # 初始化策略列表
        self.strategies = [
            MACrossStrategy(fast_period=5, slow_period=20),
            MACDStrategy(),
            KDJStrategy(),
            RSIStrategy()
        ]

    def generate_signals_for_symbol(self,
                                    symbol: str,
                                    start_date: str,
                                    end_date: str,
                                    save_to_db: bool = True) -> List[Signal]:
        """
        为单个股票生成信号

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            save_to_db: 是否保存到数据库

        Returns:
            生成的信号列表
        """
        try:
            logger.info(f"开始为 {symbol} 生成信号 ({start_date} ~ {end_date})")

            # 获取市场数据
            df = self.engine.get_daily_data(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date
            )

            if df.empty:
                logger.warning(f"未获取到 {symbol} 的数据")
                return []

            logger.info(f"获取到 {len(df)} 条数据")

            # 计算技术指标
            df = self.indicators.calculate_all_indicators(df)
            logger.info("技术指标计算完成")

            # 使用所有策略生成信号
            all_signals = []
            for strategy in self.strategies:
                signals = strategy.generate_signals(df, symbol)
                if signals:
                    logger.info(f"策略 {strategy.name} 生成了 {len(signals)} 个信号")
                    all_signals.extend(signals)

            # 保存到数据库
            if save_to_db and all_signals:
                saved_count = 0
                for signal in all_signals:
                    try:
                        self.repo.save_signal(**signal.to_dict())
                        saved_count += 1
                    except Exception as e:
                        logger.error(f"保存信号失败: {e}")

                logger.success(f"成功保存 {saved_count}/{len(all_signals)} 个信号到数据库")

            return all_signals

        except Exception as e:
            logger.error(f"生成信号失败: {e}")
            return []

    def generate_signals_for_symbols(self,
                                     symbols: List[str],
                                     start_date: str,
                                     end_date: str,
                                     save_to_db: bool = True) -> dict:
        """
        为多个股票生成信号

        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            save_to_db: 是否保存到数据库

        Returns:
            每个股票的信号结果
        """
        results = {}

        for symbol in symbols:
            signals = self.generate_signals_for_symbol(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                save_to_db=save_to_db
            )
            results[symbol] = {
                'count': len(signals),
                'signals': [s.to_dict() for s in signals]
            }

        logger.success(f"完成 {len(symbols)} 个股票的信号生成")
        return results

    def scan_market(self,
                   symbols: Optional[List[str]] = None,
                   lookback_days: int = 60,
                   save_to_db: bool = True) -> dict:
        """
        扫描市场生成最新信号

        Args:
            symbols: 股票代码列表，如果为None则使用默认列表
            lookback_days: 回溯天数
            save_to_db: 是否保存到数据库

        Returns:
            扫描结果
        """
        from datetime import datetime, timedelta

        if symbols is None:
            # 默认股票池
            symbols = [
                '688576.SH',  # 汇威科技
                '000001.SZ',  # 平安银行
                '600519.SH',  # 贵州茅台
            ]

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        logger.info(f"开始市场扫描: {len(symbols)} 个股票")
        results = self.generate_signals_for_symbols(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            save_to_db=save_to_db
        )

        # 统计
        total_signals = sum(r['count'] for r in results.values())
        logger.success(f"市场扫描完成，共生成 {total_signals} 个信号")

        return {
            'total_signals': total_signals,
            'symbols_scanned': len(symbols),
            'results': results
        }
