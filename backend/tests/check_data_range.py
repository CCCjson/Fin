"""
检查数据库中的数据范围
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from loguru import logger
from data_engine import DataEngine, init_db

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)


def main():
    init_db()
    engine = DataEngine()

    symbol = "688576.SH"

    logger.info(f"检查 {symbol} 的数据范围...")

    # 获取最新日期
    latest_date = engine.get_latest_date(symbol)
    if latest_date:
        logger.info(f"数据库中最新日期: {latest_date}")
    else:
        logger.warning("数据库中无数据")

    # 获取所有数据
    df = engine.get_daily_data(
        symbol=symbol,
        start_date="2020-01-01",
        end_date="2026-12-31"
    )

    if not df.empty:
        logger.info(f"数据总条数: {len(df)}")
        logger.info(f"数据范围: {df.index[0]} ~ {df.index[-1]}")

        # 按年份统计
        logger.info("\n按年份统计:")
        df['year'] = df.index.year
        yearly_counts = df.groupby('year').size()
        for year, count in yearly_counts.items():
            logger.info(f"  {year}年: {count} 条")

    engine.close()


if __name__ == "__main__":
    main()
