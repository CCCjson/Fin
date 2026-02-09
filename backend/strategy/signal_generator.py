"""
信号生成器
"""
from typing import Generator, List, Optional
import json
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
                                    save_to_db: bool = True,
                                    db_only: bool = False) -> List[Signal]:
        """
        为单个股票生成信号

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            save_to_db: 是否保存到数据库
            db_only: 仅查询数据库，不联网拉取数据

        Returns:
            生成的信号列表
        """
        try:
            logger.info(f"开始为 {symbol} 生成信号 ({start_date} ~ {end_date})")

            # 获取市场数据
            df = self.engine.get_daily_data(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                db_only=db_only
            )

            if df.empty:
                logger.warning(f"未获取到 {symbol} 的数据")
                return []

            logger.info(f"获取到 {len(df)} 条数据")

            # 确保 date 是列而不是索引
            if 'date' not in df.columns:
                df = df.reset_index()

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
                   save_to_db: bool = True,
                   limit: int = 100) -> dict:
        """
        扫描市场生成最新信号

        Args:
            symbols: 股票代码列表，如果为None则从数据库获取所有有数据的股票
            lookback_days: 回溯天数
            save_to_db: 是否保存到数据库
            limit: 最多扫描多少只股票

        Returns:
            扫描结果
        """
        from datetime import datetime, timedelta
        from sqlalchemy import func
        from data_engine.storage.database import get_session
        from data_engine.storage.models import DailyQuote

        session = get_session()

        if symbols is None:
            # 从数据库获取所有有数据的股票
            query = session.query(DailyQuote.symbol).distinct()
            if limit:
                query = query.limit(limit)
            symbol_rows = query.all()
            symbols = [row[0] for row in symbol_rows]
            logger.info(f"从数据库获取到 {len(symbols)} 只股票")

        # 使用数据库中实际存在的最新日期，而不是系统日期
        max_date_row = session.query(func.max(DailyQuote.date)).first()
        session.close()

        if max_date_row and max_date_row[0]:
            end_date = max_date_row[0].strftime('%Y-%m-%d') if hasattr(max_date_row[0], 'strftime') else str(max_date_row[0])
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            start_date = (end_dt - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            logger.info(f"使用数据库最新日期: {end_date}")
        else:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        logger.info(f"开始市场扫描: {len(symbols)} 个股票 ({start_date} ~ {end_date})")

        # 统计
        total_signals = 0
        buy_signals = 0
        sell_signals = 0
        results = {}

        for symbol in symbols:
            try:
                signals = self.generate_signals_for_symbol(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    save_to_db=save_to_db
                )

                symbol_buy = sum(1 for s in signals if s.signal_type.upper() == 'BUY')
                symbol_sell = sum(1 for s in signals if s.signal_type.upper() == 'SELL')

                results[symbol] = {
                    'count': len(signals),
                    'buy': symbol_buy,
                    'sell': symbol_sell
                }

                total_signals += len(signals)
                buy_signals += symbol_buy
                sell_signals += symbol_sell

            except Exception as e:
                logger.error(f"处理 {symbol} 失败: {e}")
                results[symbol] = {'count': 0, 'error': str(e)}

        logger.success(f"市场扫描完成，共生成 {total_signals} 个信号 (买入: {buy_signals}, 卖出: {sell_signals})")

        return {
            'total_signals': total_signals,
            'buy_signals': buy_signals,
            'sell_signals': sell_signals,
            'symbols_scanned': len(symbols),
            'results': results
        }

    def scan_market_stream(self,
                           symbols: Optional[List[str]] = None,
                           lookback_days: int = 60,
                           save_to_db: bool = True,
                           limit: Optional[int] = None,
                           db_only: bool = True) -> Generator[str, None, None]:
        """
        流式扫描市场，逐只股票 yield NDJSON 事件。

        事件类型:
        - start: 扫描开始，包含总数
        - progress: 每只股票扫描完成后
        - complete: 扫描全部完成，包含汇总

        Yields:
            JSON 字符串（每行一个 JSON 对象）
        """
        from datetime import datetime, timedelta
        from sqlalchemy import func
        from data_engine.storage.database import get_session
        from data_engine.storage.models import DailyQuote

        session = get_session()

        if symbols is None:
            query = session.query(DailyQuote.symbol).distinct()
            if limit:
                query = query.limit(limit)
            symbol_rows = query.all()
            symbols = [row[0] for row in symbol_rows]
            logger.info(f"从数据库获取到 {len(symbols)} 只股票")

        max_date_row = session.query(func.max(DailyQuote.date)).first()
        session.close()

        if max_date_row and max_date_row[0]:
            end_date = max_date_row[0].strftime('%Y-%m-%d') if hasattr(max_date_row[0], 'strftime') else str(max_date_row[0])
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            start_date = (end_dt - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        else:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        total = len(symbols)
        # 每 1% 发一次 progress 事件，至少间隔 1 只
        emit_interval = max(1, total // 100)
        logger.info(f"开始流式市场扫描: {total} 个股票 ({start_date} ~ {end_date})，每 {emit_interval} 只推送一次进度")

        # yield start event
        yield json.dumps({
            "event": "start",
            "total": total,
            "start_date": start_date,
            "end_date": end_date
        }, ensure_ascii=False) + "\n"

        total_signals = 0
        buy_signals = 0
        sell_signals = 0
        failed = 0

        for i, symbol in enumerate(symbols):
            try:
                signals = self.generate_signals_for_symbol(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    save_to_db=save_to_db,
                    db_only=db_only
                )
                symbol_count = len(signals)
                total_signals += symbol_count
                buy_signals += sum(1 for s in signals if s.signal_type.upper() == 'BUY')
                sell_signals += sum(1 for s in signals if s.signal_type.upper() == 'SELL')

            except Exception as e:
                logger.error(f"处理 {symbol} 失败: {e}")
                failed += 1

            # 每 emit_interval 只或最后一只时才 yield progress
            if (i + 1) % emit_interval == 0 or i + 1 == total:
                yield json.dumps({
                    "event": "progress",
                    "current": i + 1,
                    "total": total,
                    "symbol": symbol,
                    "cumulative": {
                        "total_signals": total_signals,
                        "buy_signals": buy_signals,
                        "sell_signals": sell_signals,
                        "failed": failed
                    }
                }, ensure_ascii=False) + "\n"

        logger.success(f"流式市场扫描完成，共生成 {total_signals} 个信号 (买入: {buy_signals}, 卖出: {sell_signals})")

        # yield complete event
        yield json.dumps({
            "event": "complete",
            "total_signals": total_signals,
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "symbols_scanned": total,
            "failed": failed
        }, ensure_ascii=False) + "\n"
