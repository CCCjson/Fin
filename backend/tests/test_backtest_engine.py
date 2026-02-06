"""
测试回测引擎 - 策略回测和性能评估
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
from analysis_engine import AnalysisEngine
from backtest_engine import BacktestEngine
from backtest_engine.strategies import MACrossStrategy, SignalStrategy


def main():
    logger.info("\n" + "=" * 80)
    logger.info("测试回测引擎 - 688576.SH (N三叶草)")
    logger.info("=" * 80 + "\n")

    # 初始化
    try:
        init_db()
        data_engine = DataEngine()
        analysis_engine = AnalysisEngine()
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return

    symbol = "688576.SH"

    # 获取历史数据（2024年全年）
    logger.info("【步骤1】获取历史数据...")
    try:
        df = data_engine.get_daily_data(
            symbol=symbol,
            start_date="2024-01-01",
            end_date="2024-12-31"
        )

        if df.empty:
            logger.error("未获取到数据")
            return

        logger.success(f"✓ 成功获取 {len(df)} 条数据")
        logger.info(f"数据周期: {df.index[0]} ~ {df.index[-1]}\n")

    except Exception as e:
        logger.error(f"获取数据失败: {e}")
        return

    # 添加技术指标
    logger.info("【步骤2】计算技术指标...")
    try:
        df_with_indicators = analysis_engine.add_indicators(df)
        logger.success(f"✓ 技术指标计算完成\n")
    except Exception as e:
        logger.error(f"计算指标失败: {e}")
        return

    # 测试1: 均线交叉策略回测
    logger.info("\n" + "=" * 80)
    logger.info("【测试1】均线交叉策略 (MA5 x MA20)")
    logger.info("=" * 80 + "\n")

    try:
        # 创建策略
        ma_strategy = MACrossStrategy(
            fast_period=5,
            slow_period=20,
            position_size=0.95
        )

        # 创建回测引擎
        backtest_engine = BacktestEngine(
            initial_capital=100000.0,
            commission_rate=0.0003
        )

        # 运行回测
        results = backtest_engine.run(
            symbol=symbol,
            data=df_with_indicators,
            strategy=ma_strategy
        )

        # 导出结果
        backtest_engine.export_results("./backtest_results")

    except Exception as e:
        logger.error(f"均线策略回测失败: {e}")
        import traceback
        traceback.print_exc()

    # 测试2: 信号策略回测
    logger.info("\n\n" + "=" * 80)
    logger.info("【测试2】信号策略 (基于分析引擎)")
    logger.info("=" * 80 + "\n")

    try:
        # 创建策略
        signal_strategy = SignalStrategy(
            position_size=0.95,
            min_signal_strength=0.6
        )

        # 创建回测引擎
        backtest_engine2 = BacktestEngine(
            initial_capital=100000.0,
            commission_rate=0.0003
        )

        # 运行回测
        results2 = backtest_engine2.run(
            symbol=symbol,
            data=df_with_indicators,
            strategy=signal_strategy
        )

        # 导出结果
        backtest_engine2.export_results("./backtest_results")

    except Exception as e:
        logger.error(f"信号策略回测失败: {e}")
        import traceback
        traceback.print_exc()

    # 策略对比
    logger.info("\n\n" + "=" * 80)
    logger.info("【策略对比】")
    logger.info("=" * 80 + "\n")

    if results and results2:
        logger.info("指标对比:")
        logger.info(f"{'指标':<20} {'均线策略':<15} {'信号策略':<15}")
        logger.info("-" * 50)

        metrics1 = results["metrics"]
        metrics2 = results2["metrics"]

        logger.info(f"{'总收益率':<20} {metrics1['total_return']:>14.2f}% {metrics2['total_return']:>14.2f}%")
        logger.info(f"{'年化收益率':<20} {metrics1['annualized_return']:>14.2f}% {metrics2['annualized_return']:>14.2f}%")
        logger.info(f"{'波动率':<20} {metrics1['volatility']:>14.2f}% {metrics2['volatility']:>14.2f}%")
        logger.info(f"{'夏普比率':<20} {metrics1['sharpe_ratio']:>14.2f}  {metrics2['sharpe_ratio']:>14.2f}")
        logger.info(f"{'最大回撤':<20} {metrics1['max_drawdown']['max_drawdown_pct']:>14.2f}% {metrics2['max_drawdown']['max_drawdown_pct']:>14.2f}%")
        logger.info(f"{'胜率':<20} {metrics1['win_rate']:>14.2f}% {metrics2['win_rate']:>14.2f}%")
        logger.info(f"{'交易次数':<20} {metrics1['num_trades']:>14}  {metrics2['num_trades']:>14}")

        # 判断最优策略
        logger.info("\n最优策略:")
        if metrics1['sharpe_ratio'] > metrics2['sharpe_ratio']:
            logger.success("  均线交叉策略 (更高的夏普比率)")
        elif metrics2['sharpe_ratio'] > metrics1['sharpe_ratio']:
            logger.success("  信号策略 (更高的夏普比率)")
        else:
            logger.info("  两个策略表现相当")

    # 清理
    data_engine.close()

    logger.info("\n" + "=" * 80)
    logger.success("🎉 回测引擎测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
