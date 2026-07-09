"""
生成交易信号脚本 - 使用分析引擎检测信号并保存到数据库
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

import pandas as pd
from datetime import date, timedelta
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import DailyQuote
from data_engine.storage.history_repository import HistoryRepository
from analysis_engine.engine import AnalysisEngine


def get_stock_data(symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
    """从数据库获取股票数据"""
    session = get_session()
    try:
        quotes = session.query(DailyQuote).filter(
            DailyQuote.symbol == symbol,
            DailyQuote.date >= start_date,
            DailyQuote.date <= end_date
        ).order_by(DailyQuote.date).all()

        if not quotes:
            return pd.DataFrame()

        data = []
        for q in quotes:
            data.append({
                'date': q.date,
                'open': q.open,
                'high': q.high,
                'low': q.low,
                'close': q.close,
                'volume': q.volume
            })

        df = pd.DataFrame(data)
        df.set_index('date', inplace=True)
        return df
    finally:
        session.close()


def generate_signals_for_symbol(symbol: str, start_date: date, end_date: date):
    """为单个股票生成信号"""
    logger.info(f"处理股票: {symbol}")

    # 获取数据
    df = get_stock_data(symbol, start_date, end_date)
    if df.empty:
        logger.warning(f"  {symbol} 无数据")
        return 0

    logger.info(f"  获取到 {len(df)} 条数据")

    # 分析并检测信号
    engine = AnalysisEngine()
    result = engine.analyze(symbol, df, detect_signals=True, detect_patterns=False)

    signals = result.get('signals', [])
    if not signals:
        logger.info(f"  {symbol} 未检测到信号")
        return 0

    # 保存信号到数据库
    repo = HistoryRepository()
    saved_count = 0

    for signal in signals:
        try:
            repo.save_signal(
                symbol=signal.symbol,
                date=signal.timestamp.date() if hasattr(signal.timestamp, 'date') else signal.timestamp,
                signal_type=signal.signal_type.value.upper(),
                strength=signal.strength,
                price=signal.price,
                strategy=f"analysis_engine_{signal.reason.replace(' ', '_')}",
                reasons=[signal.reason],
                entry_price=signal.price,
                stop_loss=signal.price * 0.95 if signal.signal_type.value == 'BUY' else signal.price * 1.05,
                take_profit=signal.price * 1.10 if signal.signal_type.value == 'BUY' else signal.price * 0.90,
                position_size="5%"
            )
            saved_count += 1
        except Exception as e:
            logger.error(f"  保存信号失败: {e}")

    repo.close()
    logger.success(f"  {symbol} 保存 {saved_count} 个信号")
    return saved_count


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("开始生成交易信号")
    logger.info("=" * 60)

    # 设置日期范围 - 使用数据库中实际存在的数据范围
    # 数据库数据: 2023-01-03 到 2025-02-05
    end_date = date(2025, 2, 5)
    start_date = date(2024, 10, 1)  # 最近几个月的数据用于分析

    # 从数据库获取所有股票代码
    session = get_session()
    symbols = session.query(DailyQuote.symbol).distinct().limit(50).all()
    symbols = [s[0] for s in symbols]
    session.close()

    logger.info(f"找到 {len(symbols)} 只股票")

    total_signals = 0
    for symbol in symbols:
        try:
            count = generate_signals_for_symbol(symbol, start_date, end_date)
            total_signals += count
        except Exception as e:
            logger.error(f"处理 {symbol} 失败: {e}")

    logger.info("=" * 60)
    logger.success(f"信号生成完成! 共生成 {total_signals} 个信号")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
