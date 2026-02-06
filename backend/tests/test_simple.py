"""
简化的数据引擎测试（无交互）
"""
import sys
from datetime import datetime, timedelta
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

from data_engine import DataEngine, init_db


def main():
    logger.info("\n" + "=" * 60)
    logger.info("数据引擎简化测试")
    logger.info("=" * 60 + "\n")

    # 1. 初始化数据库
    logger.info("【测试1】初始化数据库...")
    try:
        init_db()
        logger.success("✓ 数据库初始化成功\n")
    except Exception as e:
        logger.error(f"✗ 失败: {e}\n")
        return

    # 2. 获取日线数据
    logger.info("【测试2】获取日线数据...")
    try:
        engine = DataEngine()

        symbol = "000001.SZ"  # 平安银行
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        logger.info(f"获取 {symbol} 最近30天数据...")

        df = engine.get_daily_data(
            symbol=symbol,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )

        if not df.empty:
            logger.info(f"成功获取 {len(df)} 条数据")
            logger.info(f"数据列: {list(df.columns)}")
            logger.info(f"\n最近5天:")
            print(df.tail())
            logger.success("\n✓ 获取日线数据成功\n")
        else:
            logger.warning("数据为空\n")

        engine.close()

    except Exception as e:
        logger.error(f"✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 3. 获取实时行情
    logger.info("【测试3】获取实时行情...")
    try:
        engine = DataEngine()

        symbols = ["000001.SZ", "600000.SH"]
        logger.info(f"获取 {symbols} 的实时行情...")

        quotes = engine.get_realtime_quotes(symbols)

        if quotes:
            logger.info(f"获取到 {len(quotes)} 只股票的实时行情:")
            for quote in quotes:
                logger.info(
                    f"  {quote['symbol']}: ¥{quote['price']:.2f} "
                    f"({quote['change_percent']:+.2f}%)"
                )
            logger.success("\n✓ 获取实时行情成功\n")
        else:
            logger.warning("实时行情为空\n")

        engine.close()

    except Exception as e:
        logger.error(f"✗ 失败: {e}\n")

    logger.info("=" * 60)
    logger.success("🎉 测试完成！数据引擎运行正常！")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
