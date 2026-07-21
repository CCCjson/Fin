"""
历史记录API演示
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import requests
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

BASE_URL = "http://127.0.0.1:8000"


def demo_signals():
    """演示信号历史查询"""
    logger.info("\n" + "=" * 80)
    logger.info("【演示1】交易信号历史")
    logger.info("=" * 80)

    # 查询信号
    logger.info("\n1.1 查询所有信号")
    response = requests.get(f"{BASE_URL}/history/signals?limit=10")
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 查询到 {result['count']} 条信号")
        for signal in result['signals'][:3]:
            logger.info(
                f"  {signal['date']} {signal['symbol']} {signal['signal_type']} "
                f"强度={signal['strength']:.2f}"
            )

    # 查询特定股票的信号
    logger.info("\n1.2 查询特定股票信号")
    response = requests.get(
        f"{BASE_URL}/history/signals",
        params={"symbol": "688576.SH", "limit": 5}
    )
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 688576.SH 的信号: {result['count']} 条")

    # 信号统计
    logger.info("\n1.3 信号统计")
    response = requests.get(
        f"{BASE_URL}/history/signals/statistics",
        params={"days": 30}
    )
    if response.status_code == 200:
        stats = response.json()
        logger.success(f"✓ 30天信号统计:")
        logger.info(f"  总信号: {stats['total_signals']}")
        logger.info(f"  买入: {stats['buy_signals']}")
        logger.info(f"  卖出: {stats['sell_signals']}")
        logger.info(f"  平均强度: {stats['avg_strength']:.2f}")


def demo_backtests():
    """演示回测历史查询"""
    logger.info("\n" + "=" * 80)
    logger.info("【演示2】回测历史记录")
    logger.info("=" * 80)

    # 查询回测任务
    logger.info("\n2.1 查询回测任务列表")
    response = requests.get(f"{BASE_URL}/history/backtests?limit=10")
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 查询到 {result['count']} 个回测任务")
        for task in result['tasks'][:3]:
            logger.info(
                f"  {task['task_id']}: {task['name'] or task['strategy_type']} "
                f"状态={task['status']}"
            )

    # 查询已完成的回测
    logger.info("\n2.2 查询已完成的回测")
    response = requests.get(
        f"{BASE_URL}/history/backtests",
        params={"status": "completed"}
    )
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 已完成的回测: {result['count']} 个")

        # 获取第一个回测的详细结果
        if result['tasks']:
            task_id = result['tasks'][0]['task_id']

            logger.info(f"\n2.3 查询回测结果详情: {task_id}")
            detail_response = requests.get(
                f"{BASE_URL}/history/backtests/{task_id}"
            )
            if detail_response.status_code == 200:
                detail = detail_response.json()
                metrics = detail['metrics']
                logger.success("✓ 回测结果:")
                logger.info(f"  总收益率: {metrics.get('total_return_pct', 0):.2f}%")
                logger.info(f"  夏普比率: {metrics.get('sharpe_ratio', 0):.2f}")
                logger.info(f"  最大回撤: {metrics.get('max_drawdown_pct', 0):.2f}%")
                logger.info(f"  胜率: {metrics.get('win_rate', 0):.2f}%")
                logger.info(f"  总交易: {metrics.get('total_trades', 0)}")

    # 回测对比
    logger.info("\n2.4 回测结果对比")
    response = requests.get(f"{BASE_URL}/history/backtests/comparison")
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 获取 {result['count']} 个回测结果进行对比")
        for item in result['comparison'][:3]:
            logger.info(
                f"  {item['strategy']}: 收益率={item['total_return_pct']:.2f}% "
                f"夏普={item['sharpe_ratio']:.2f}"
            )


def demo_orders_and_trades():
    """演示订单和成交历史"""
    logger.info("\n" + "=" * 80)
    logger.info("【演示3】订单和成交历史")
    logger.info("=" * 80)

    account_id = "paper_account_001"

    # 查询订单
    logger.info("\n3.1 查询订单历史")
    response = requests.get(
        f"{BASE_URL}/history/orders",
        params={"account_id": account_id, "limit": 10}
    )
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 查询到 {result['count']} 个订单")
        for order in result['orders'][:3]:
            logger.info(
                f"  {order['symbol']} {order['side']} {order['quantity']}股 "
                f"状态={order['status']}"
            )

    # 订单统计
    logger.info("\n3.2 订单统计")
    response = requests.get(
        f"{BASE_URL}/history/orders/statistics",
        params={"account_id": account_id, "days": 30}
    )
    if response.status_code == 200:
        stats = response.json()
        logger.success(f"✓ 30天订单统计:")
        logger.info(f"  总订单: {stats['total_orders']}")
        logger.info(f"  已成交: {stats['filled']}")
        logger.info(f"  成交率: {stats['fill_rate']:.1f}%")

    # 查询成交记录
    logger.info("\n3.3 查询成交记录")
    response = requests.get(
        f"{BASE_URL}/history/trades",
        params={"account_id": account_id, "limit": 10}
    )
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 查询到 {result['count']} 条成交记录")
        for trade in result['trades'][:3]:
            logger.info(
                f"  {trade['symbol']} {trade['direction']} "
                f"{trade['quantity']}@{trade['price']:.2f}"
            )

    # 成交统计
    logger.info("\n3.4 成交统计")
    response = requests.get(
        f"{BASE_URL}/history/trades/statistics",
        params={"account_id": account_id, "days": 30}
    )
    if response.status_code == 200:
        stats = response.json()
        logger.success(f"✓ 30天成交统计:")
        logger.info(f"  总成交: {stats['total_trades']}")
        logger.info(f"  买入: {stats['buy_trades']}")
        logger.info(f"  卖出: {stats['sell_trades']}")
        logger.info(f"  总成交额: {stats['total_amount']:,.2f}")
        logger.info(f"  总手续费: {stats['total_commission']:.2f}")


def main():
    """运行演示"""
    logger.info("\n" + "=" * 80)
    logger.info("🚀 历史记录API演示")
    logger.info("=" * 80)

    try:
        # 演示信号历史
        demo_signals()

        # 演示回测历史
        demo_backtests()

        # 演示订单和成交
        demo_orders_and_trades()

        logger.info("\n" + "=" * 80)
        logger.success("🎉 演示完成！")
        logger.info("=" * 80)
        logger.info("\n历史记录API功能:")
        logger.info("  ✓ /history/signals - 查询交易信号")
        logger.info("  ✓ /history/signals/statistics - 信号统计")
        logger.info("  ✓ /history/backtests - 查询回测任务")
        logger.info("  ✓ /history/backtests/{task_id} - 回测详情")
        logger.info("  ✓ /history/backtests/comparison - 回测对比")
        logger.info("  ✓ /history/orders - 查询订单")
        logger.info("  ✓ /history/orders/statistics - 订单统计")
        logger.info("  ✓ /history/trades - 查询成交")
        logger.info("  ✓ /history/trades/statistics - 成交统计")
        logger.info("\n完整API文档: http://127.0.0.1:8000/docs")
        logger.info("=" * 80)

    except requests.exceptions.ConnectionError:
        logger.error("\n❌ 无法连接到API服务器")
        logger.error("请先启动服务: bash restart.sh --backend")


if __name__ == "__main__":
    main()
