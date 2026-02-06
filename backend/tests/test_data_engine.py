"""
数据引擎测试脚本
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

# 导入数据引擎
from data_engine import DataEngine, init_db


def test_database():
    """测试1: 数据库初始化"""
    logger.info("=" * 60)
    logger.info("测试1: 数据库初始化")
    logger.info("=" * 60)

    try:
        init_db()
        logger.success("✓ 数据库初始化成功")
        return True
    except Exception as e:
        logger.error(f"✗ 数据库初始化失败: {e}")
        return False


def test_search_stocks():
    """测试2: 搜索股票"""
    logger.info("=" * 60)
    logger.info("测试2: 搜索股票")
    logger.info("=" * 60)

    try:
        engine = DataEngine()

        # 搜索平安银行
        results = engine.search_stocks("平安", market="a_share")

        if results:
            logger.info(f"搜索到 {len(results)} 只股票:")
            for stock in results[:5]:
                logger.info(f"  - {stock['symbol']}: {stock['name']}")
            logger.success("✓ 搜索股票成功")
            return True
        else:
            logger.warning("搜索结果为空")
            return False

    except Exception as e:
        logger.error(f"✗ 搜索股票失败: {e}")
        return False
    finally:
        engine.close()


def test_fetch_daily_data():
    """测试3: 获取日线数据"""
    logger.info("=" * 60)
    logger.info("测试3: 获取日线数据")
    logger.info("=" * 60)

    try:
        engine = DataEngine()

        # 获取平安银行最近30天的数据
        symbol = "000001.SZ"
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        logger.info(f"获取 {symbol} 的数据...")

        df = engine.get_daily_data(
            symbol=symbol,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )

        if not df.empty:
            logger.info(f"获取到 {len(df)} 条数据")
            logger.info(f"数据列: {df.columns.tolist()}")
            logger.info(f"数据范围: {df.index[0]} ~ {df.index[-1]}")
            logger.info("\n最近5天数据:")
            logger.info(f"\n{df.tail()}")
            logger.success("✓ 获取日线数据成功")
            return True
        else:
            logger.warning("数据为空")
            return False

    except Exception as e:
        logger.error(f"✗ 获取日线数据失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        engine.close()


def test_update_stock_list():
    """测试4: 更新股票列表"""
    logger.info("=" * 60)
    logger.info("测试4: 更新股票列表 (可选，较慢)")
    logger.info("=" * 60)

    user_input = input("是否执行股票列表更新? (y/n): ")
    if user_input.lower() != 'y':
        logger.info("跳过股票列表更新")
        return True

    try:
        engine = DataEngine()

        count = engine.update_stock_list(market="a_share")

        logger.info(f"更新了 {count} 只股票信息")
        logger.success("✓ 更新股票列表成功")
        return True

    except Exception as e:
        logger.error(f"✗ 更新股票列表失败: {e}")
        return False
    finally:
        engine.close()


def test_realtime_quotes():
    """测试5: 获取实时行情"""
    logger.info("=" * 60)
    logger.info("测试5: 获取实时行情")
    logger.info("=" * 60)

    try:
        engine = DataEngine()

        symbols = ["000001.SZ", "600000.SH", "000002.SZ"]
        logger.info(f"获取 {symbols} 的实时行情...")

        quotes = engine.get_realtime_quotes(symbols)

        if quotes:
            logger.info(f"获取到 {len(quotes)} 只股票的实时行情:")
            for quote in quotes:
                logger.info(
                    f"  - {quote['symbol']}: ¥{quote['price']:.2f} "
                    f"({quote['change_percent']:+.2f}%)"
                )
            logger.success("✓ 获取实时行情成功")
            return True
        else:
            logger.warning("实时行情为空")
            return False

    except Exception as e:
        logger.error(f"✗ 获取实时行情失败: {e}")
        return False
    finally:
        engine.close()


def main():
    """主测试函数"""
    logger.info("\n" + "=" * 60)
    logger.info("数据引擎功能测试")
    logger.info("=" * 60 + "\n")

    results = []

    # 执行测试
    results.append(("数据库初始化", test_database()))
    results.append(("搜索股票", test_search_stocks()))
    results.append(("获取日线数据", test_fetch_daily_data()))
    results.append(("更新股票列表", test_update_stock_list()))
    results.append(("获取实时行情", test_realtime_quotes()))

    # 输出测试结果
    logger.info("\n" + "=" * 60)
    logger.info("测试结果汇总")
    logger.info("=" * 60)

    passed = 0
    failed = 0

    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        logger.info(f"{name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1

    logger.info("=" * 60)
    logger.info(f"总计: {passed + failed} 个测试, {passed} 个通过, {failed} 个失败")
    logger.info("=" * 60)

    if failed == 0:
        logger.success("\n🎉 所有测试通过！数据引擎运行正常！")
    else:
        logger.warning(f"\n⚠️  有 {failed} 个测试失败，请检查！")


if __name__ == "__main__":
    main()
