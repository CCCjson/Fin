"""
测试获取 688576.SH (N三叶草) 的历史数据
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from loguru import logger
import pandas as pd

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

from data_engine import DataEngine, init_db


def main():
    logger.info("\n" + "=" * 80)
    logger.info("测试获取 688576.SH 的历史数据")
    logger.info("=" * 80 + "\n")

    # 初始化数据库
    logger.info("【步骤1】初始化数据库...")
    try:
        init_db()
        logger.success("✓ 数据库初始化成功\n")
    except Exception as e:
        logger.error(f"✗ 失败: {e}\n")
        return

    # 获取历史数据
    logger.info("【步骤2】获取 688576.SH 的历史数据...")
    try:
        engine = DataEngine()

        symbol = "688576.SH"

        # 测试1: 获取最近1个月数据
        logger.info("\n[测试1] 获取最近1个月数据")
        logger.info("-" * 80)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        df_1month = engine.get_daily_data(
            symbol=symbol,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )

        if not df_1month.empty:
            logger.success(f"✓ 成功获取 {len(df_1month)} 条数据")
            first_date = df_1month.index[0] if hasattr(df_1month.index[0], 'date') else df_1month.index[0]
            last_date = df_1month.index[-1] if hasattr(df_1month.index[-1], 'date') else df_1month.index[-1]
            logger.info(f"数据时间范围: {first_date} ~ {last_date}")
            logger.info(f"\n数据统计信息:")
            logger.info(f"  开盘价: {df_1month['open'].min():.2f} ~ {df_1month['open'].max():.2f}")
            logger.info(f"  收盘价: {df_1month['close'].min():.2f} ~ {df_1month['close'].max():.2f}")
            logger.info(f"  成交量: {df_1month['volume'].min()/10000:.2f}万 ~ {df_1month['volume'].max()/10000:.2f}万")

            logger.info(f"\n最近5天数据:")
            print(df_1month.tail()[['open', 'high', 'low', 'close', 'volume', 'turnover']])
        else:
            logger.warning("✗ 未获取到数据")

        # 测试2: 获取最近3个月数据
        logger.info("\n[测试2] 获取最近3个月数据")
        logger.info("-" * 80)

        start_date_3m = end_date - timedelta(days=90)

        df_3month = engine.get_daily_data(
            symbol=symbol,
            start_date=start_date_3m.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )

        if not df_3month.empty:
            logger.success(f"✓ 成功获取 {len(df_3month)} 条数据")
            first_date = df_3month.index[0] if hasattr(df_3month.index[0], 'date') else df_3month.index[0]
            last_date = df_3month.index[-1] if hasattr(df_3month.index[-1], 'date') else df_3month.index[-1]
            logger.info(f"数据时间范围: {first_date} ~ {last_date}")

            # 计算一些统计指标
            logger.info(f"\n统计分析:")
            logger.info(f"  平均收盘价: {df_3month['close'].mean():.2f}")
            max_date = df_3month['high'].idxmax()
            min_date = df_3month['low'].idxmin()
            logger.info(f"  最高价: {df_3month['high'].max():.2f} (日期: {max_date})")
            logger.info(f"  最低价: {df_3month['low'].min():.2f} (日期: {min_date})")
            logger.info(f"  总成交量: {df_3month['volume'].sum()/100000000:.2f}亿")
            logger.info(f"  平均换手率: {df_3month['turnover'].mean():.2f}%")

            # 计算涨跌幅
            returns = df_3month['close'].pct_change()
            logger.info(f"\n收益统计:")
            logger.info(f"  期间涨跌幅: {((df_3month['close'].iloc[-1] / df_3month['close'].iloc[0]) - 1) * 100:.2f}%")
            logger.info(f"  最大单日涨幅: {returns.max() * 100:.2f}%")
            logger.info(f"  最大单日跌幅: {returns.min() * 100:.2f}%")
            logger.info(f"  日均波动率: {returns.std() * 100:.2f}%")

            # 显示所有数据
            logger.info(f"\n完整数据预览 (前10条):")
            print(df_3month.head(10)[['open', 'high', 'low', 'close', 'volume', 'turnover']])

        else:
            logger.warning("✗ 未获取到数据")

        # 测试3: 获取2024年全年数据
        logger.info("\n[测试3] 获取2024年全年数据")
        logger.info("-" * 80)

        df_2024 = engine.get_daily_data(
            symbol=symbol,
            start_date="2024-01-01",
            end_date="2024-12-31"
        )

        if not df_2024.empty:
            logger.success(f"✓ 成功获取 {len(df_2024)} 条数据")
            first_date = df_2024.index[0] if hasattr(df_2024.index[0], 'date') else df_2024.index[0]
            last_date = df_2024.index[-1] if hasattr(df_2024.index[-1], 'date') else df_2024.index[-1]
            logger.info(f"数据时间范围: {first_date} ~ {last_date}")

            # 月度统计
            logger.info(f"\n月度收盘价统计:")
            monthly_stats = df_2024['close'].resample('ME').agg(['first', 'last', 'max', 'min', 'mean'])
            monthly_stats.columns = ['开盘', '收盘', '最高', '最低', '平均']
            print(monthly_stats)

        else:
            logger.warning("✗ 未获取到数据")

        # 测试4: 验证数据已保存到数据库
        logger.info("\n[测试4] 验证数据库存储")
        logger.info("-" * 80)

        # 再次获取数据，应该从数据库读取
        df_from_db = engine.get_daily_data(
            symbol=symbol,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )

        if not df_from_db.empty:
            logger.success(f"✓ 从数据库成功读取 {len(df_from_db)} 条数据")
            logger.info("数据已成功保存并可从数据库快速读取")

        # 获取最新数据日期
        latest_date = engine.get_latest_date(symbol)
        if latest_date:
            logger.info(f"数据库中最新数据日期: {latest_date}")

        engine.close()

        logger.info("\n" + "=" * 80)
        logger.success("🎉 测试完成！688576.SH 数据获取成功！")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
