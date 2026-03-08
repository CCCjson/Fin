"""
信号生成器
"""
from typing import Generator, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# 并行扫描的默认线程数
_DEFAULT_WORKERS = 8


def _process_symbol(symbol: str,
                    start_date: str,
                    end_date: str,
                    save_to_db: bool,
                    db_only: bool) -> tuple:
    """
    线程安全的单股票信号生成（每次调用创建独立的 DB 连接）

    Returns:
        (symbol, signals_list, error_msg_or_None)
    """
    engine = DataEngine()
    indicators = TechnicalIndicators()
    strategies = [
        MACrossStrategy(fast_period=5, slow_period=20),
        MACDStrategy(),
        KDJStrategy(),
        RSIStrategy()
    ]
    repo = None
    try:
        df = engine.get_daily_data(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            db_only=db_only
        )
        if df.empty:
            return (symbol, [], None)

        if 'date' not in df.columns:
            df = df.reset_index()

        df = indicators.calculate_all_indicators(df)

        all_signals: List[Signal] = []
        for strategy in strategies:
            signals = strategy.generate_signals(df, symbol)
            if signals:
                all_signals.extend(signals)

        # 保存到数据库（使用独立 session）
        if save_to_db and all_signals:
            repo = HistoryRepository()
            for signal in all_signals:
                try:
                    repo.save_signal(**signal.to_dict())
                except Exception as e:
                    logger.error(f"保存信号失败: {e}")

        return (symbol, all_signals, None)

    except Exception as e:
        logger.error(f"处理 {symbol} 失败: {e}")
        return (symbol, [], str(e))
    finally:
        engine.close()
        if repo:
            repo.close()


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

    def _resolve_scan_params(self, symbols, lookback_days, limit):
        """解析扫描参数：获取股票列表和日期范围"""
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
            logger.info(f"使用数据库最新日期: {end_date}")
        else:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        return symbols, start_date, end_date

    def scan_intraday(
        self,
        symbols: List[str],
        bar_count: int = 240,
        period: int = 1,
        save_to_db: bool = True,
    ) -> dict:
        """
        盘中扫描：基于 pytdx 分钟线数据生成信号

        Args:
            symbols: 股票代码列表
            bar_count: 拉取 K 线根数，默认 240（≈1 个交易日的 1 分钟线）
            period: K 线周期（分钟），支持 1/5/15/30/60
            save_to_db: 是否保存信号到数据库

        Returns:
            与 scan_market() 一致的 result dict
        """
        from data_engine.fetchers.pytdx_fetcher import PytdxFetcher

        logger.info(f"盘中扫描开始: {len(symbols)} 只股票, {period}分钟线, {bar_count}根")

        fetcher = PytdxFetcher()
        try:
            bars_dict = fetcher.fetch_minute_bars_batch(symbols, period=period, count=bar_count)
        finally:
            fetcher.close()

        total_signals = 0
        buy_signals = 0
        sell_signals = 0
        results = {}

        for symbol in symbols:
            df = bars_dict.get(symbol)
            if df is None or df.empty:
                results[symbol] = {"count": 0, "buy": 0, "sell": 0}
                continue

            try:
                df = self.indicators.calculate_all_indicators(df)

                all_signals: List[Signal] = []
                for strategy in self.strategies:
                    signals = strategy.generate_signals(df, symbol)
                    if signals:
                        all_signals.extend(signals)

                if save_to_db and all_signals:
                    for signal in all_signals:
                        try:
                            self.repo.save_signal(**signal.to_dict())
                        except Exception as e:
                            logger.error(f"保存信号失败: {e}")

                sym_buy = sum(1 for s in all_signals if s.signal_type.upper() == "BUY")
                sym_sell = sum(1 for s in all_signals if s.signal_type.upper() == "SELL")

                results[symbol] = {"count": len(all_signals), "buy": sym_buy, "sell": sym_sell}
                total_signals += len(all_signals)
                buy_signals += sym_buy
                sell_signals += sym_sell

            except Exception as e:
                logger.error(f"盘中扫描处理 {symbol} 失败: {e}")
                results[symbol] = {"count": 0, "buy": 0, "sell": 0, "error": str(e)}

        logger.success(
            f"盘中扫描完成: 扫描 {len(symbols)} 只，数据获取 {len(bars_dict)} 只，"
            f"信号 {total_signals} 个 (买入 {buy_signals}, 卖出 {sell_signals})"
        )

        return {
            "total_signals": total_signals,
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "symbols_scanned": len(symbols),
            "results": results,
        }

    def scan_market(self,
                   symbols: Optional[List[str]] = None,
                   lookback_days: int = 60,
                   save_to_db: bool = True,
                   limit: int = 100) -> dict:
        """
        扫描市场生成最新信号（并行处理）

        Args:
            symbols: 股票代码列表，如果为None则从数据库获取所有有数据的股票
            lookback_days: 回溯天数
            save_to_db: 是否保存到数据库
            limit: 最多扫描多少只股票

        Returns:
            扫描结果
        """
        symbols, start_date, end_date = self._resolve_scan_params(symbols, lookback_days, limit)

        logger.info(f"开始市场扫描: {len(symbols)} 个股票 ({start_date} ~ {end_date})")

        total_signals = 0
        buy_signals = 0
        sell_signals = 0
        results = {}
        max_workers = min(_DEFAULT_WORKERS, len(symbols)) if symbols else 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _process_symbol, symbol, start_date, end_date, save_to_db, False
                ): symbol
                for symbol in symbols
            }

            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    _, signals, error = future.result()
                    if error:
                        results[symbol] = {'count': 0, 'error': error}
                        continue

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

    def detect_signal_gaps(self, lookback_days: int = 30) -> List[str]:
        """
        检测缺失信号的交易日。

        比较 DailyQuote 中的交易日和 Signal 中已有信号的日期，
        找出有行情数据但没有信号记录的日期。

        Args:
            lookback_days: 回溯天数（只检查最近 N 天）

        Returns:
            缺失信号的日期列表 (YYYY-MM-DD)，按时间升序
        """
        from datetime import datetime, timedelta
        from sqlalchemy import func, distinct
        from data_engine.storage.database import get_session
        from data_engine.storage.models import DailyQuote, Signal as SignalModel

        session = get_session()
        try:
            cutoff = (datetime.now() - timedelta(days=lookback_days)).date()

            # 数据库中有行情的交易日（去重）
            quote_dates = session.query(distinct(DailyQuote.date)).filter(
                DailyQuote.date >= cutoff
            ).all()
            quote_date_set = {row[0] for row in quote_dates}

            # 数据库中有信号的日期（去重）
            signal_dates = session.query(distinct(SignalModel.date)).filter(
                SignalModel.date >= cutoff
            ).all()
            signal_date_set = {row[0] for row in signal_dates}

            # 有行情但无信号的日期
            missing = sorted(quote_date_set - signal_date_set)

            # 过滤掉周末（理论上不应该有，但以防万一）
            missing = [d for d in missing if d.weekday() < 5]

            logger.info(f"信号缺口检测: 行情日 {len(quote_date_set)} 天, "
                        f"信号日 {len(signal_date_set)} 天, "
                        f"缺失 {len(missing)} 天")

            return [d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d) for d in missing]
        finally:
            session.close()

    def backfill_signals_stream(self,
                                lookback_days: int = 30,
                                save_to_db: bool = True,
                                db_only: bool = True,
                                limit: Optional[int] = None) -> Generator[str, None, None]:
        """
        流式回补缺失信号。

        检测最近 lookback_days 内有行情但无信号的交易日，
        逐日回补信号生成。

        Yields:
            NDJSON 事件流
        """
        from datetime import datetime, timedelta

        missing_dates = self.detect_signal_gaps(lookback_days)
        if not missing_dates:
            yield json.dumps({
                "event": "complete",
                "message": "无需回补，所有交易日均已有信号",
                "backfilled_days": 0,
                "total_signals": 0,
            }, ensure_ascii=False) + "\n"
            return

        # 获取股票列表
        symbols, _, _ = self._resolve_scan_params(None, lookback_days=60, limit=limit)
        total_days = len(missing_dates)

        yield json.dumps({
            "event": "start",
            "message": f"检测到 {total_days} 个交易日缺失信号，开始回补...",
            "missing_dates": missing_dates,
            "symbols_count": len(symbols),
        }, ensure_ascii=False) + "\n"

        grand_total_signals = 0
        grand_buy = 0
        grand_sell = 0

        for day_idx, target_date in enumerate(missing_dates):
            # 回溯 60 个自然日的数据来计算指标，end_date 设为目标日
            target_dt = datetime.strptime(target_date, '%Y-%m-%d')
            start_date = (target_dt - timedelta(days=60)).strftime('%Y-%m-%d')

            day_signals = 0
            day_buy = 0
            day_sell = 0
            day_failed = 0

            max_workers = min(_DEFAULT_WORKERS, len(symbols)) if symbols else 1

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _process_symbol, symbol, start_date, target_date, save_to_db, db_only
                    ): symbol
                    for symbol in symbols
                }
                for future in as_completed(futures):
                    try:
                        _, signals, error = future.result()
                        if error:
                            day_failed += 1
                        else:
                            day_signals += len(signals)
                            day_buy += sum(1 for s in signals if s.signal_type.upper() == 'BUY')
                            day_sell += sum(1 for s in signals if s.signal_type.upper() == 'SELL')
                    except Exception:
                        day_failed += 1

            grand_total_signals += day_signals
            grand_buy += day_buy
            grand_sell += day_sell

            yield json.dumps({
                "event": "day_complete",
                "date": target_date,
                "day_index": day_idx + 1,
                "total_days": total_days,
                "day_signals": day_signals,
                "day_buy": day_buy,
                "day_sell": day_sell,
                "day_failed": day_failed,
                "cumulative_signals": grand_total_signals,
            }, ensure_ascii=False) + "\n"

            logger.info(f"回补 {target_date} 完成: {day_signals} 信号 "
                        f"(买{day_buy}/卖{day_sell}), 失败{day_failed}")

        yield json.dumps({
            "event": "complete",
            "message": f"信号回补完成！共回补 {total_days} 天",
            "backfilled_days": total_days,
            "total_signals": grand_total_signals,
            "buy_signals": grand_buy,
            "sell_signals": grand_sell,
        }, ensure_ascii=False) + "\n"

        logger.success(f"信号回补完成: {total_days} 天, "
                       f"{grand_total_signals} 信号 (买{grand_buy}/卖{grand_sell})")

    def scan_market_stream(self,
                           symbols: Optional[List[str]] = None,
                           lookback_days: int = 60,
                           save_to_db: bool = True,
                           limit: Optional[int] = None,
                           db_only: bool = True) -> Generator[str, None, None]:
        """
        流式扫描市场，并行处理，逐批 yield NDJSON 事件。

        事件类型:
        - start: 扫描开始，包含总数
        - progress: 每批股票扫描完成后
        - complete: 扫描全部完成，包含汇总

        Yields:
            JSON 字符串（每行一个 JSON 对象）
        """
        symbols, start_date, end_date = self._resolve_scan_params(symbols, lookback_days, limit)

        total = len(symbols)
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
        completed = 0

        max_workers = min(_DEFAULT_WORKERS, total) if total else 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _process_symbol, symbol, start_date, end_date, save_to_db, db_only
                ): symbol
                for symbol in symbols
            }

            for future in as_completed(futures):
                symbol = futures[future]
                completed += 1

                try:
                    _, signals, error = future.result()
                    if error:
                        failed += 1
                    else:
                        total_signals += len(signals)
                        buy_signals += sum(1 for s in signals if s.signal_type.upper() == 'BUY')
                        sell_signals += sum(1 for s in signals if s.signal_type.upper() == 'SELL')
                except Exception as e:
                    logger.error(f"处理 {symbol} 失败: {e}")
                    failed += 1

                # 每 emit_interval 只或最后一只时才 yield progress
                if completed % emit_interval == 0 or completed == total:
                    yield json.dumps({
                        "event": "progress",
                        "current": completed,
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
