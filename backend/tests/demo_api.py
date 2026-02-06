"""
API功能演示
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


def main():
    logger.info("\n" + "=" * 80)
    logger.info("🚀 量化交易系统API演示")
    logger.info("=" * 80)

    # 1. 获取股票数据
    logger.info("\n【1. 数据模块】获取股票历史数据")
    response = requests.post(f"{BASE_URL}/data/daily", json={
        "symbol": "688576.SH",
        "start_date": "2025-12-01",
        "end_date": "2025-12-31"
    })
    if response.status_code == 200:
        result = response.json()
        logger.success(f"✓ 获取到 {result['count']} 条数据")
        logger.info(f"  最新数据: {result['data'][-1]['date']} 收盘价={result['data'][-1]['close']}")

    # 2. 初始化交易账户
    logger.info("\n【2. 交易模块】初始化Paper Trading账户")
    response = requests.post(f"{BASE_URL}/trading/init", params={
        "initial_cash": 500000.0,
        "commission_rate": 0.0003
    })
    if response.status_code == 200:
        logger.success(f"✓ {response.json()['message']}")

    # 3. 更新市场价格
    logger.info("\n【3. 交易模块】更新市场价格")
    response = requests.post(f"{BASE_URL}/trading/price/update", json={
        "symbol": "688576.SH",
        "price": 63.50
    })
    if response.status_code == 200:
        logger.success(f"✓ {response.json()['message']}")

    # 4. 提交买单
    logger.info("\n【4. 交易模块】提交买单")
    response = requests.post(f"{BASE_URL}/trading/order", json={
        "symbol": "688576.SH",
        "action": "BUY",
        "quantity": 1000,
        "price": None  # 市价单
    })
    if response.status_code == 200:
        order = response.json()
        logger.success(f"✓ {order['message']}")
        logger.info(f"  订单ID: {order['order_id']}")
        logger.info(f"  成交价: {order['filled_price']:.2f}")
        logger.info(f"  手续费: {order['commission']:.2f}")

    # 5. 查看账户信息
    logger.info("\n【5. 交易模块】查看账户信息")
    response = requests.get(f"{BASE_URL}/trading/account")
    if response.status_code == 200:
        account = response.json()
        logger.success("✓ 账户状态:")
        logger.info(f"  现金: {account['cash']:,.2f}")
        logger.info(f"  市值: {account['market_value']:,.2f}")
        logger.info(f"  总资产: {account['total_value']:,.2f}")

    # 6. 更新价格（模拟上涨）
    logger.info("\n【6. 交易模块】模拟价格上涨")
    response = requests.post(f"{BASE_URL}/trading/price/update", json={
        "symbol": "688576.SH",
        "price": 65.00
    })
    if response.status_code == 200:
        logger.success(f"✓ 价格更新为 65.00")

    # 7. 查看持仓
    logger.info("\n【7. 交易模块】查看持仓盈亏")
    response = requests.get(f"{BASE_URL}/trading/position/688576.SH")
    if response.status_code == 200:
        pos = response.json()
        logger.success("✓ 持仓信息:")
        logger.info(f"  股票: {pos['symbol']}")
        logger.info(f"  数量: {pos['quantity']}")
        logger.info(f"  成本: {pos['avg_cost']:.2f}")
        logger.info(f"  现价: {pos['current_price']:.2f}")
        logger.info(f"  盈亏: {pos['unrealized_pnl']:,.2f} ({pos['unrealized_pnl_pct']:.2f}%)")

    # 8. 卖出部分
    logger.info("\n【8. 交易模块】卖出部分持仓")
    response = requests.post(f"{BASE_URL}/trading/order", json={
        "symbol": "688576.SH",
        "action": "SELL",
        "quantity": 500,
        "price": None
    })
    if response.status_code == 200:
        order = response.json()
        logger.success(f"✓ {order['message']}")
        logger.info(f"  卖出价: {order['filled_price']:.2f}")

    # 9. 最终账户状态
    logger.info("\n【9. 交易模块】最终账户状态")
    response = requests.get(f"{BASE_URL}/trading/account")
    if response.status_code == 200:
        account = response.json()
        logger.success("✓ 最终账户:")
        logger.info(f"  现金: {account['cash']:,.2f}")
        logger.info(f"  市值: {account['market_value']:,.2f}")
        logger.info(f"  总资产: {account['total_value']:,.2f}")
        profit = account['total_value'] - 500000.0
        logger.info(f"  总盈亏: {profit:,.2f} ({profit/500000*100:.2f}%)")

    # 10. API文档
    logger.info("\n" + "=" * 80)
    logger.success("🎉 演示完成！")
    logger.info("=" * 80)
    logger.info("\n更多API功能请访问:")
    logger.info("  📖 Swagger文档: http://127.0.0.1:8000/docs")
    logger.info("  📖 ReDoc文档: http://127.0.0.1:8000/redoc")
    logger.info("\n支持的功能模块:")
    logger.info("  ✓ 数据模块 - 获取历史数据、实时行情、股票列表")
    logger.info("  ✓ 分析模块 - 计算技术指标、检测信号、识别形态")
    logger.info("  ✓ 回测模块 - 运行策略回测、查看绩效")
    logger.info("  ✓ 交易模块 - Paper Trading、下单、查询持仓")
    logger.info("  ✓ 监控模块 - 交易日志、订单跟踪、绩效统计、告警管理")
    logger.info("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        logger.error("\n❌ 无法连接到API服务器")
        logger.error("请先启动API服务器:")
        logger.error("  python start_api.py")
