"""
测试历史记录数据库功能
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from datetime import datetime, date, timedelta
import uuid
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

from data_engine import init_db
from data_engine.storage.history_repository import HistoryRepository


def test_signals():
    """测试信号表"""
    logger.info("\n" + "=" * 80)
    logger.info("【测试1】交易信号功能")
    logger.info("=" * 80)

    repo = HistoryRepository()

    # 保存信号
    logger.info("\n1.1 保存交易信号")
    signal = repo.save_signal(
        symbol="688576.SH",
        date=date.today(),
        signal_type="BUY",
        strength=0.85,
        price=63.50,
        strategy="MA_CROSS",
        reasons=["5日均线上穿20日均线", "成交量放大"],
        entry_price=63.50,
        stop_loss=60.00,
        take_profit=68.00,
        position_size="50%"
    )
    logger.success(f"✓ 信号已保存: {signal.signal_id}")

    # 查询信号
    logger.info("\n1.2 查询交易信号")
    signals = repo.get_signals(symbol="688576.SH", limit=10)
    logger.success(f"✓ 查询到 {len(signals)} 条信号")
    for s in signals[:3]:
        logger.info(f"  - {s.date} {s.signal_type} 强度={s.strength:.2f}")

    # 统计信号
    logger.info("\n1.3 信号统计")
    stats = repo.get_signal_statistics(symbol="688576.SH", days=30)
    logger.success(f"✓ 信号统计:")
    logger.info(f"  总信号数: {stats['total_signals']}")
    logger.info(f"  买入信号: {stats['buy_signals']}")
    logger.info(f"  卖出信号: {stats['sell_signals']}")
    logger.info(f"  平均强度: {stats['avg_strength']:.2f}")

    repo.close()


def test_backtest():
    """测试回测表"""
    logger.info("\n" + "=" * 80)
    logger.info("【测试2】回测记录功能")
    logger.info("=" * 80)

    repo = HistoryRepository()

    # 创建回测任务
    logger.info("\n2.1 创建回测任务")
    task_id = f"backtest_{uuid.uuid4().hex[:8]}"
    task = repo.save_backtest_task(
        task_id=task_id,
        name="均线交叉策略回测",
        strategy_type="MA_CROSS",
        symbols=["688576.SH"],
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
        initial_capital=1000000.0,
        strategy_params={"fast_period": 5, "slow_period": 20},
        commission_rate=0.0003
    )
    logger.success(f"✓ 回测任务已创建: {task.task_id}")

    # 更新状态
    logger.info("\n2.2 更新回测状态")
    repo.update_backtest_status(task_id, "running")
    logger.success("✓ 状态更新为 running")

    # 保存回测结果
    logger.info("\n2.3 保存回测结果")
    result = repo.save_backtest_result(
        task_id=task_id,
        metrics={
            "total_return": 125432.50,
            "total_return_pct": 12.54,
            "annual_return": 8.32,
            "final_value": 1125432.50,
            "max_drawdown": 85000.0,
            "max_drawdown_pct": 8.5,
            "volatility": 18.5,
            "sharpe_ratio": 1.45,
            "sortino_ratio": 1.78,
            "total_trades": 45,
            "winning_trades": 28,
            "losing_trades": 17,
            "win_rate": 62.22,
            "profit_factor": 1.85
        },
        daily_records=[
            {"date": "2024-01-01", "value": 1000000},
            {"date": "2024-01-02", "value": 1005000}
        ],
        trade_records=[
            {"date": "2024-01-05", "action": "BUY", "symbol": "688576.SH", "price": 60.0}
        ]
    )
    logger.success(f"✓ 回测结果已保存")

    # 更新完成状态
    repo.update_backtest_status(task_id, "completed")
    logger.success("✓ 状态更新为 completed")

    # 查询回测任务
    logger.info("\n2.4 查询回测任务")
    tasks = repo.get_backtest_tasks(status="completed", limit=5)
    logger.success(f"✓ 查询到 {len(tasks)} 个已完成的回测任务")
    for t in tasks:
        logger.info(f"  - {t.task_id}: {t.name or t.strategy_type}")

    # 获取回测结果
    logger.info("\n2.5 获取回测结果")
    result = repo.get_backtest_result(task_id)
    if result:
        logger.success(f"✓ 回测结果:")
        logger.info(f"  总收益率: {result.total_return_pct:.2f}%")
        logger.info(f"  夏普比率: {result.sharpe_ratio:.2f}")
        logger.info(f"  最大回撤: {result.max_drawdown_pct:.2f}%")
        logger.info(f"  胜率: {result.win_rate:.2f}%")
        logger.info(f"  总交易数: {result.total_trades}")

    # 回测对比
    logger.info("\n2.6 回测结果对比")
    comparison = repo.get_backtest_comparison()
    logger.success(f"✓ 获取到 {len(comparison)} 个回测结果进行对比")

    repo.close()


def test_orders_and_trades():
    """测试订单和成交表"""
    logger.info("\n" + "=" * 80)
    logger.info("【测试3】订单和成交记录功能")
    logger.info("=" * 80)

    repo = HistoryRepository()
    account_id = "paper_account_001"

    # 保存订单
    logger.info("\n3.1 保存订单")
    order_id = f"order_{uuid.uuid4().hex[:8]}"
    order = repo.save_order(
        order_id=order_id,
        account_id=account_id,
        symbol="688576.SH",
        side="BUY",
        order_type="MARKET",
        quantity=100,
        status="PENDING",
        strategy="MA_CROSS",
        reason="均线金叉信号"
    )
    logger.success(f"✓ 订单已保存: {order.order_id}")

    # 更新订单状态
    logger.info("\n3.2 更新订单状态")
    repo.update_order_status(
        order_id=order_id,
        status="FILLED",
        filled_quantity=100,
        avg_fill_price=63.50,
        commission=1.91
    )
    logger.success("✓ 订单状态更新为 FILLED")

    # 保存成交记录
    logger.info("\n3.3 保存成交记录")
    trade_id = f"trade_{uuid.uuid4().hex[:8]}"
    trade = repo.save_trade(
        trade_id=trade_id,
        order_id=order_id,
        account_id=account_id,
        symbol="688576.SH",
        direction="long",
        quantity=100,
        price=63.50,
        commission=1.91,
        amount=6350.0,
        slippage=0.05
    )
    logger.success(f"✓ 成交记录已保存: {trade.trade_id}")

    # 查询订单
    logger.info("\n3.4 查询订单")
    orders = repo.get_orders(account_id=account_id, limit=10)
    logger.success(f"✓ 查询到 {len(orders)} 个订单")
    for o in orders[:3]:
        logger.info(f"  - {o.symbol} {o.side} {o.quantity}股 状态={o.status}")

    # 订单统计
    logger.info("\n3.5 订单统计")
    order_stats = repo.get_order_statistics(account_id=account_id, days=30)
    logger.success(f"✓ 订单统计:")
    logger.info(f"  总订单数: {order_stats['total_orders']}")
    logger.info(f"  已成交: {order_stats['filled']}")
    logger.info(f"  已拒绝: {order_stats['rejected']}")
    logger.info(f"  成交率: {order_stats['fill_rate']:.1f}%")

    # 查询成交记录
    logger.info("\n3.6 查询成交记录")
    trades = repo.get_trades(account_id=account_id, limit=10)
    logger.success(f"✓ 查询到 {len(trades)} 条成交记录")
    for t in trades[:3]:
        logger.info(f"  - {t.symbol} {t.direction} {t.quantity}@{t.price:.2f}")

    # 成交统计
    logger.info("\n3.7 成交统计")
    trade_stats = repo.get_trade_statistics(account_id=account_id, days=30)
    logger.success(f"✓ 成交统计:")
    logger.info(f"  总成交数: {trade_stats['total_trades']}")
    logger.info(f"  买入成交: {trade_stats['buy_trades']}")
    logger.info(f"  卖出成交: {trade_stats['sell_trades']}")
    logger.info(f"  总成交额: {trade_stats['total_amount']:,.2f}")
    logger.info(f"  总手续费: {trade_stats['total_commission']:.2f}")

    repo.close()


def main():
    """运行所有测试"""
    logger.info("\n" + "=" * 80)
    logger.info("🚀 历史记录数据库功能测试")
    logger.info("=" * 80)

    # 初始化数据库（会创建新表）
    logger.info("\n初始化数据库...")
    init_db()
    logger.success("✓ 数据库初始化完成\n")

    try:
        # 测试信号
        test_signals()

        # 测试回测
        test_backtest()

        # 测试订单和成交
        test_orders_and_trades()

        logger.info("\n" + "=" * 80)
        logger.success("🎉 所有测试完成！")
        logger.info("=" * 80)
        logger.info("\n新增的数据库表:")
        logger.info("  ✓ signals - 交易信号表")
        logger.info("  ✓ backtest_tasks - 回测任务表")
        logger.info("  ✓ backtest_results - 回测结果表")
        logger.info("  ✓ orders - 订单记录表")
        logger.info("  ✓ trades - 成交记录表")
        logger.info("\n这些表已可用于:")
        logger.info("  - 跟踪交易信号历史和准确率")
        logger.info("  - 保存和对比回测结果")
        logger.info("  - 完整的交易审计和统计")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
