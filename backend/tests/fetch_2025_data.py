"""
获取2025年数据
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

    logger.info(f"尝试获取 {symbol} 2025年的数据...")

    # 获取2025年数据
    df = engine.get_daily_data(
        symbol=symbol,
        start_date="2025-01-01",
        end_date="2025-12-31"
    )

    if not df.empty:
        logger.success(f"✓ 成功获取 {len(df)} 条数据")
        logger.info(f"数据范围: {df.index[0]} ~ {df.index[-1]}")
        logger.info(f"\n前5条数据:")
        print(df.head())
        logger.info(f"\n后5条数据:")
        print(df.tail())
    else:
        logger.warning("未获取到2025年数据，可能该股票在2025年停牌或退市")

    engine.close()


if __name__ == "__main__":
    main()
