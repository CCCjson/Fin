"""
测试交易监控模块
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import time
from datetime import datetime
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

from trading_engine.brokers.paper_broker import PaperBroker
from trading_engine.monitor import (
    OrderTracker,
    TradeLogger,
    PerformanceMonitor,
    AlertManager,
    AlertType
)
from data_engine import DataEngine, init_db


def main():
    logger.info("\n" + "=" * 80)
    logger.info("测试交易监控模块")
    logger.info("=" * 80 + "\n")

    # 初始化
    init_db()
    data_engine = DataEngine()

    # 创建监控组件
    logger.info("【步骤1】初始化监控组件...")
    order_tracker = OrderTracker()
    trade_logger = TradeLogger()
    performance_monitor = PerformanceMonitor(initial_capital=100000.0)
    alert_manager = AlertManager()

    # 添加告警处理器
    def alert_handler(alert):
        logger.info(f"  📢 {alert}")

    alert_manager.add_handler(AlertType.WARNING, alert_handler)
    alert_manager.add_handler(AlertType.ERROR, alert_handler)

    logger.success("✓ 监控组件初始化完成\n")

    # 创建模拟交易器
    logger.info("【步骤2】创建模拟交易账户...")
    broker = PaperBroker(initial_cash=100000.0)
    symbol = "688576.SH"

    # 获取历史数据
    df = data_engine.get_daily_data(
        symbol=symbol,
        start_date="2025-12-20",
        end_date="2025-12-31"
    )

    if df.empty:
        logger.error("未获取到数据")
        return

    logger.success(f"✓ 获取到 {len(df)} 条数据\n")

    # 模拟交易场景
    logger.info("【步骤3】模拟交易并监控...")
    logger.info("=" * 80)

    for i, (date, row) in enumerate(df.tail(8).iterrows()):
        price = row["close"]
        broker.update_market_price(symbol, price)

        logger.info(f"\n[{date.strftime('%Y-%m-%d')}] 价格: {price:.2f}")

        # 第1天：买入
        if i == 0:
            logger.info("  → 买入操作")
            order = broker.submit_order(symbol, "BUY", 800, None)

            # 订单跟踪
            order_tracker.add_order(order)

            # 交易日志
            trade_logger.log_trade(
                symbol=symbol,
                action="BUY",
                quantity=800,
                price=order.filled_price,
                commission=order.commission,
                order_id=order.order_id,
                strategy="手动交易"
            )

        # 第3天：价格下跌，触发告警
        elif i == 2:
            pos = broker.get_position(symbol)
            if pos:
                loss_pct = (price - pos.avg_cost) / pos.avg_cost * 100
                if loss_pct < -2:
                    alert_manager.send_position_alert(
                        symbol,
                        f"持仓亏损 {abs(loss_pct):.2f}%",
                        AlertType.WARNING
                    )

        # 第5天：加仓
        elif i == 4:
            logger.info("  → 加仓操作")
            order = broker.submit_order(symbol, "BUY", 400, None)
            order_tracker.add_order(order)
            trade_logger.log_trade(
                symbol, "BUY", 400, order.filled_price,
                order.commission, order.order_id, "手动交易"
            )

        # 第7天：部分卖出
        elif i == 6:
            logger.info("  → 减仓操作")
            order = broker.submit_order(symbol, "SELL", 600, None)
            order_tracker.add_order(order)
            trade_logger.log_trade(
                symbol, "SELL", 600, order.filled_price,
                order.commission, order.order_id, "手动交易"
            )

        # 记录绩效快照
        account = broker.get_account_info()
        positions = {s: broker.get_position(s) for s in [symbol] if broker.get_position(s)}

        performance_monitor.take_snapshot(
            timestamp=date,
            cash=account["cash"],
            market_value=account["market_value"],
            total_value=account["total_value"],
            positions=positions
        )

        # 显示账户状态
        logger.info(f"    账户: 现金={account['cash']:,.2f}, 总资产={account['total_value']:,.2f}")

        time.sleep(0.5)  # 模拟时间流逝

    # 查看监控结果
    logger.info("\n\n" + "=" * 80)
    logger.info("【步骤4】监控结果汇总")
    logger.info("=" * 80)

    # 订单跟踪统计
    logger.info("\n1. 订单跟踪统计:")
    logger.info(f"  {order_tracker}")
    stats = order_tracker.get_statistics()
    logger.info(f"  成交率: {stats['fill_rate']:.1f}%")
    logger.info(f"  已成交: {stats['filled']} 笔")
    logger.info(f"  已拒绝: {stats['rejected']} 笔")

    # 交易日志统计
    logger.info("\n2. 交易日志统计:")
    logger.info(f"  {trade_logger}")
    log_stats = trade_logger.get_statistics()
    logger.info(f"  买入交易: {log_stats['buy_trades']} 笔")
    logger.info(f"  卖出交易: {log_stats['sell_trades']} 笔")
    logger.info(f"  总手续费: {log_stats['total_commission']:.2f}")
    logger.info(f"  总成交额: {log_stats['total_value']:,.2f}")

    # 绩效监控
    logger.info("\n3. 绩效监控:")
    performance_monitor.print_summary()

    perf_stats = performance_monitor.get_statistics()
    if perf_stats:
        logger.info(f"\n  详细指标:")
        logger.info(f"    交易天数: {perf_stats['num_trading_days']}")
        logger.info(f"    总收益率: {perf_stats['total_return_pct']:.2f}%")
        logger.info(f"    波动率: {perf_stats['volatility']:.2f}%")
        logger.info(f"    夏普比率: {perf_stats['sharpe_ratio']:.2f}")
        logger.info(f"    最大回撤: {perf_stats['max_drawdown_pct']:.2f}%")
        logger.info(f"    胜率: {perf_stats['win_rate']:.2f}%")

    # 告警管理
    logger.info("\n4. 告警管理:")
    logger.info(f"  {alert_manager}")
    alert_stats = alert_manager.get_statistics()
    logger.info(f"  INFO: {alert_stats['by_type'][AlertType.INFO]} 条")
    logger.info(f"  WARNING: {alert_stats['by_type'][AlertType.WARNING]} 条")
    logger.info(f"  ERROR: {alert_stats['by_type'][AlertType.ERROR]} 条")

    unacknowledged = alert_manager.get_unacknowledged_alerts()
    if unacknowledged:
        logger.info(f"\n  未确认告警 ({len(unacknowledged)} 条):")
        for alert in unacknowledged[:5]:  # 显示前5条
            logger.warning(f"    {alert.title}: {alert.message}")

    # 导出日志
    logger.info("\n\n【步骤5】导出交易日志...")
    trade_logger.export_to_csv("./logs/trades/trade_summary.csv")
    logger.success("✓ 交易日志已导出\n")

    # 清理
    data_engine.close()

    logger.info("=" * 80)
    logger.success("🎉 交易监控模块测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
