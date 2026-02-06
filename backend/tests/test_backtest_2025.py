"""
测试回测引擎 - 使用2025年数据
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from loguru import logger

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
    logger.info("回测引擎 - 688576.SH (2025年数据)")
    logger.info("=" * 80 + "\n")

    # 初始化
    init_db()
    data_engine = DataEngine()
    analysis_engine = AnalysisEngine()

    symbol = "688576.SH"

    # 获取2025年数据
    logger.info("【步骤1】获取2025年历史数据...")
    df = data_engine.get_daily_data(
        symbol=symbol,
        start_date="2025-01-01",
        end_date="2025-12-31"
    )

    if df.empty:
        logger.error("未获取到数据")
        return

    logger.success(f"✓ 成功获取 {len(df)} 条数据")
    logger.info(f"数据周期: {df.index[0]} ~ {df.index[-1]}")
    logger.info(f"价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    logger.info(f"期间涨跌幅: {((df['close'].iloc[-1] / df['close'].iloc[0]) - 1) * 100:.2f}%\n")

    # 添加技术指标
    logger.info("【步骤2】计算技术指标...")
    df_with_indicators = analysis_engine.add_indicators(df)
    logger.success(f"✓ 技术指标计算完成\n")

    # 测试1: 均线交叉策略
    logger.info("\n" + "=" * 80)
    logger.info("【策略1】均线交叉策略 (MA5 x MA20)")
    logger.info("=" * 80 + "\n")

    ma_strategy = MACrossStrategy(fast_period=5, slow_period=20, position_size=0.95)
    backtest1 = BacktestEngine(initial_capital=100000.0, commission_rate=0.0003)
    results1 = backtest1.run(symbol=symbol, data=df_with_indicators, strategy=ma_strategy)

    # 测试2: 信号策略
    logger.info("\n\n" + "=" * 80)
    logger.info("【策略2】信号策略 (基于分析引擎)")
    logger.info("=" * 80 + "\n")

    signal_strategy = SignalStrategy(position_size=0.95, min_signal_strength=0.6)
    backtest2 = BacktestEngine(initial_capital=100000.0, commission_rate=0.0003)
    results2 = backtest2.run(symbol=symbol, data=df_with_indicators, strategy=signal_strategy)

    # 策略对比
    logger.info("\n\n" + "=" * 80)
    logger.info("【策略对比 - 2025年】")
    logger.info("=" * 80 + "\n")

    if results1 and results2:
        metrics1 = results1["metrics"]
        metrics2 = results2["metrics"]

        logger.info("性能指标对比:")
        logger.info(f"{'指标':<20} {'均线策略':<15} {'信号策略':<15} {'买入持有':<15}")
        logger.info("-" * 65)

        # 计算买入持有策略
        buy_hold_return = ((df['close'].iloc[-1] / df['close'].iloc[0]) - 1) * 100

        logger.info(f"{'总收益率':<20} {metrics1['total_return']:>14.2f}% {metrics2['total_return']:>14.2f}% {buy_hold_return:>14.2f}%")
        logger.info(f"{'年化收益率':<20} {metrics1['annualized_return']:>14.2f}% {metrics2['annualized_return']:>14.2f}% {buy_hold_return:>14.2f}%")
        logger.info(f"{'波动率':<20} {metrics1['volatility']:>14.2f}% {metrics2['volatility']:>14.2f}% {'N/A':>14}")
        logger.info(f"{'夏普比率':<20} {metrics1['sharpe_ratio']:>14.2f}  {metrics2['sharpe_ratio']:>14.2f}  {'N/A':>14}")
        logger.info(f"{'最大回撤':<20} {metrics1['max_drawdown']['max_drawdown_pct']:>14.2f}% {metrics2['max_drawdown']['max_drawdown_pct']:>14.2f}% {'N/A':>14}")
        logger.info(f"{'胜率':<20} {metrics1['win_rate']:>14.2f}% {metrics2['win_rate']:>14.2f}% {'N/A':>14}")
        logger.info(f"{'盈亏比':<20} {metrics1['profit_factor']:>14.2f}  {metrics2['profit_factor']:>14.2f}  {'N/A':>14}")
        logger.info(f"{'交易次数':<20} {metrics1['num_trades']:>14}  {metrics2['num_trades']:>14}  {1:>14}")

        logger.info("\n总结:")
        logger.info(f"  股票2025年表现: {buy_hold_return:+.2f}%")

        # 判断最优策略
        if metrics1['total_return'] > buy_hold_return and metrics1['total_return'] > metrics2['total_return']:
            logger.success("  🏆 均线交叉策略表现最佳")
        elif metrics2['total_return'] > buy_hold_return and metrics2['total_return'] > metrics1['total_return']:
            logger.success("  🏆 信号策略表现最佳")
        elif buy_hold_return > metrics1['total_return'] and buy_hold_return > metrics2['total_return']:
            logger.info("  🏆 买入持有策略表现最佳（量化策略未跑赢市场）")
        else:
            logger.info("  策略表现接近")

        # 风险调整后收益
        logger.info("\n风险调整后收益 (夏普比率):")
        if metrics1['sharpe_ratio'] > metrics2['sharpe_ratio']:
            logger.success(f"  🏆 均线策略 (夏普比率: {metrics1['sharpe_ratio']:.2f})")
        elif metrics2['sharpe_ratio'] > metrics1['sharpe_ratio']:
            logger.success(f"  🏆 信号策略 (夏普比率: {metrics2['sharpe_ratio']:.2f})")
        else:
            logger.info("  两个策略风险调整后收益相当")

    # 导出结果
    backtest1.export_results("./backtest_results_2025")
    backtest2.export_results("./backtest_results_2025")

    data_engine.close()

    logger.info("\n" + "=" * 80)
    logger.success("🎉 2025年回测完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
