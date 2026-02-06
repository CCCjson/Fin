"""
测试策略引擎
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from loguru import logger
from datetime import datetime, timedelta

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

from data_engine import init_db
from strategy import SignalGenerator

# 初始化数据库
init_db()


def test_single_symbol():
    """测试单个股票信号生成"""
    logger.info("\n" + "=" * 80)
    logger.info("【测试1】单个股票信号生成")
    logger.info("=" * 80)

    generator = SignalGenerator()

    # 生成信号
    signals = generator.generate_signals_for_symbol(
        symbol='688576.SH',
        start_date='2025-01-01',
        end_date='2025-12-31',
        save_to_db=True
    )

    logger.info(f"\n生成了 {len(signals)} 个信号:")
    for signal in signals:
        logger.info(f"  - {signal.signal_type} 信号，强度: {signal.strength:.2f}")
        logger.info(f"    策略: {signal.strategy}")
        logger.info(f"    价格: ¥{signal.price:.2f}")
        logger.info(f"    原因: {', '.join(signal.reasons)}")


def test_batch_symbols():
    """测试批量股票信号生成"""
    logger.info("\n" + "=" * 80)
    logger.info("【测试2】批量股票信号生成")
    logger.info("=" * 80)

    generator = SignalGenerator()

    symbols = ['688576.SH', '000001.SZ']

    results = generator.generate_signals_for_symbols(
        symbols=symbols,
        start_date='2025-01-01',
        end_date='2025-12-31',
        save_to_db=True
    )

    for symbol, result in results.items():
        logger.info(f"\n{symbol}: {result['count']} 个信号")


def test_market_scan():
    """测试市场扫描"""
    logger.info("\n" + "=" * 80)
    logger.info("【测试3】市场扫描")
    logger.info("=" * 80)

    generator = SignalGenerator()

    # 扫描市场
    results = generator.scan_market(
        symbols=['688576.SH'],
        lookback_days=60,
        save_to_db=True
    )

    logger.success(f"\n扫描完成:")
    logger.info(f"  总信号数: {results['total_signals']}")
    logger.info(f"  扫描股票数: {results['symbols_scanned']}")


if __name__ == '__main__':
    logger.info("开始测试策略引擎...")

    # 运行测试
    test_single_symbol()
    test_batch_symbols()
    test_market_scan()

    logger.success("\n所有测试完成!")
