"""
测试 OpenCTP 连接
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import time
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

from trading_engine.brokers.openctp_broker import OpenCtpBroker
from trading_engine.config import OPENCTP_CONFIG


def main():
    logger.info("\n" + "=" * 80)
    logger.info("测试 OpenCTP 连接 (7x24 测试环境)")
    logger.info("=" * 80 + "\n")

    # 创建 OpenCTP 接口
    logger.info("【步骤1】初始化 OpenCTP 接口...")
    broker = OpenCtpBroker(**OPENCTP_CONFIG)

    # 连接服务器
    logger.info("\n【步骤2】连接行情和交易服务器...")
    logger.info(f"账号: {OPENCTP_CONFIG['user_id']}")
    logger.info(f"行情服务器: {OPENCTP_CONFIG['md_address']}")
    logger.info(f"交易服务器: {OPENCTP_CONFIG['td_address']}\n")

    success = broker.connect()

    if not success:
        logger.error("连接失败")
        return

    # 查询账户信息
    logger.info("\n【步骤3】查询账户信息...")
    time.sleep(2)  # 等待数据返回
    account = broker.get_account_info()
    logger.info(f"  现金: {account.get('cash', 0):,.2f}")
    logger.info(f"  总资产: {account.get('total_value', 0):,.2f}")
    logger.info(f"  保证金: {account.get('margin', 0):,.2f}")
    logger.info(f"  手续费: {account.get('commission', 0):,.2f}")

    # 查询持仓
    logger.info("\n【步骤4】查询持仓...")
    positions = broker.get_positions()
    if positions:
        logger.info(f"当前持仓 {len(positions)} 个:")
        for pos in positions:
            logger.info(f"  {pos.symbol}: {pos.quantity}手, 成本={pos.avg_cost:.2f}, 盈亏={pos.unrealized_pnl:.2f}")
    else:
        logger.info("  无持仓")

    # 订阅行情
    logger.info("\n【步骤5】订阅行情...")
    # 常用合约示例
    test_symbols = [
        "rb2505",  # 螺纹钢主力
        "IF2502",  # 沪深300股指期货
        "600000",  # 浦发银行（如果支持股票）
    ]

    broker.subscribe_market_data(test_symbols)
    logger.info("等待行情推送...")

    # 等待行情数据
    for i in range(10):
        time.sleep(1)
        if broker.market_data:
            break

    # 显示行情
    if broker.market_data:
        logger.info(f"\n收到 {len(broker.market_data)} 个合约的行情:")
        for symbol, data in broker.market_data.items():
            logger.info(
                f"  {symbol}: 最新价={data.get('last_price', 0):.2f}, "
                f"买价={data.get('bid_price', 0):.2f}, "
                f"卖价={data.get('ask_price', 0):.2f}, "
                f"时间={data.get('update_time', 'N/A')}"
            )
    else:
        logger.warning("未收到行情数据（可能合约代码不正确或不在交易时间）")

    # 测试下单（仅演示，不实际成交）
    logger.info("\n【步骤6】测试报单功能（演示）...")
    logger.warning("注意: 这是模拟测试，不会实际成交")

    # 示例：买入1手螺纹钢
    # order = broker.submit_order(
    #     symbol="rb2505",
    #     action="BUY",
    #     quantity=1,
    #     price=3500.0  # 限价单
    # )
    # logger.info(f"订单状态: {order.status.value}")

    logger.info("跳过实际报单测试")

    # 保持连接一段时间
    logger.info("\n【步骤7】保持连接，监听回调...")
    logger.info("按 Ctrl+C 退出\n")

    try:
        for i in range(60):
            time.sleep(1)
            # 定期刷新账户信息
            if i % 10 == 0:
                broker.query_account()
    except KeyboardInterrupt:
        logger.info("\n收到退出信号")

    # 断开连接
    logger.info("\n【步骤8】断开连接...")
    broker.disconnect()

    logger.info("\n" + "=" * 80)
    logger.success("🎉 OpenCTP 测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
