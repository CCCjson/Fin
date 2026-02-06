"""
测试 Paper Trading 模拟交易
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

from trading_engine.brokers.paper_broker import PaperBroker
from data_engine import DataEngine, init_db


def main():
    logger.info("\n" + "=" * 80)
    logger.info("测试 Paper Trading 模拟交易")
    logger.info("=" * 80 + "\n")

    # 初始化
    init_db()
    data_engine = DataEngine()

    # 创建模拟交易器
    logger.info("【步骤1】创建 Paper Trading 账户...")
    broker = PaperBroker(
        initial_cash=1000000.0,
        commission_rate=0.0003,
        slippage=0.0001
    )
    logger.success("✓ 账户创建成功\n")

    # 获取历史数据作为市场价格
    logger.info("【步骤2】获取市场数据...")
    symbol = "688576.SH"
    df = data_engine.get_daily_data(
        symbol=symbol,
        start_date="2025-12-01",
        end_date="2025-12-31"
    )

    if df.empty:
        logger.error("未获取到数据")
        return

    logger.success(f"✓ 获取到 {len(df)} 条数据\n")

    # 模拟交易流程
    logger.info("【步骤3】开始模拟交易...")
    logger.info("-" * 80)

    # 使用最近几天的数据模拟交易
    for i, (date, row) in enumerate(df.tail(10).iterrows()):
        price = row["close"]
        broker.update_market_price(symbol, price)

        logger.info(f"\n[{date.strftime('%Y-%m-%d')}] 收盘价: {price:.2f}")

        # 第1天：买入
        if i == 0:
            logger.info("  → 执行买入操作...")
            order = broker.submit_order(
                symbol=symbol,
                action="BUY",
                quantity=1000,
                price=None  # 市价单
            )
            logger.info(f"    订单状态: {order.status.value}")

        # 第5天：加仓
        elif i == 4:
            logger.info("  → 执行加仓操作...")
            order = broker.submit_order(
                symbol=symbol,
                action="BUY",
                quantity=500,
                price=None
            )
            logger.info(f"    订单状态: {order.status.value}")

        # 第8天：部分卖出
        elif i == 7:
            logger.info("  → 执行减仓操作...")
            order = broker.submit_order(
                symbol=symbol,
                action="SELL",
                quantity=800,
                price=None
            )
            logger.info(f"    订单状态: {order.status.value}")

        # 显示当前账户状态
        account = broker.get_account_info()
        logger.info(f"    现金: {account['cash']:,.2f}")
        logger.info(f"    市值: {account['market_value']:,.2f}")
        logger.info(f"    总资产: {account['total_value']:,.2f}")
        logger.info(f"    收益率: {account['return_pct']:.2f}%")

    # 最终账户报告
    logger.info("\n" + "=" * 80)
    logger.info("【步骤4】最终账户报告")
    logger.info("=" * 80)

    account = broker.get_account_info()
    logger.info(f"\n初始资金: {broker.initial_cash:,.2f}")
    logger.info(f"当前现金: {account['cash']:,.2f}")
    logger.info(f"持仓市值: {account['market_value']:,.2f}")
    logger.info(f"总资产: {account['total_value']:,.2f}")
    logger.info(f"未实现盈亏: {account['unrealized_pnl']:,.2f}")
    logger.info(f"累计手续费: {account['total_commission']:.2f}")
    logger.info(f"总交易次数: {account['total_trades']}")
    logger.info(f"收益率: {account['return_pct']:.2f}%")

    # 持仓明细
    logger.info("\n当前持仓:")
    positions = broker.get_positions()
    if positions:
        for pos in positions:
            logger.info(f"  {pos.symbol}:")
            logger.info(f"    数量: {pos.quantity}")
            logger.info(f"    成本: {pos.avg_cost:.2f}")
            logger.info(f"    现价: {pos.current_price:.2f}")
            logger.info(f"    市值: {pos.market_value:.2f}")
            logger.info(f"    盈亏: {pos.unrealized_pnl:.2f} ({pos.unrealized_pnl/pos.market_value*100:.2f}%)")
    else:
        logger.info("  无持仓")

    # 订单历史
    logger.info("\n交易记录:")
    orders = broker.get_orders()
    for order in orders:
        action_emoji = "🔺" if order.action == "BUY" else "🔻"
        logger.info(
            f"  {action_emoji} {order.action} {order.symbol} "
            f"{order.filled_quantity}股 @ {order.filled_price:.2f} "
            f"({order.status.value})"
        )

    # 清理
    data_engine.close()

    logger.info("\n" + "=" * 80)
    logger.success("🎉 Paper Trading 测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
