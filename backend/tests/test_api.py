"""
测试API接口
"""

import pytest

pytestmark = pytest.mark.integration
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import requests
import json
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

BASE_URL = "http://127.0.0.1:8000"


def test_health():
    """测试健康检查"""
    logger.info("\n" + "=" * 80)
    logger.info("测试1: 健康检查")
    logger.info("=" * 80)

    response = requests.get(f"{BASE_URL}/health")
    logger.info(f"状态码: {response.status_code}")
    logger.info(f"响应: {response.json()}")


def test_get_data():
    """测试获取数据"""
    logger.info("\n" + "=" * 80)
    logger.info("测试2: 获取股票数据")
    logger.info("=" * 80)

    data = {
        "symbol": "688576.SH",
        "start_date": "2025-12-01",
        "end_date": "2025-12-31"
    }

    response = requests.post(f"{BASE_URL}/data/daily", json=data)
    logger.info(f"状态码: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        logger.success(f"获取到 {result['count']} 条数据")
        logger.info(f"前3条数据:")
        for item in result['data'][:3]:
            logger.info(f"  {item['date']}: 收盘={item['close']}")
    else:
        logger.error(f"错误: {response.json()}")


def test_calculate_indicators():
    """测试计算指标"""
    logger.info("\n" + "=" * 80)
    logger.info("测试3: 计算技术指标")
    logger.info("=" * 80)

    data = {
        "symbol": "688576.SH",
        "start_date": "2025-11-01",
        "end_date": "2025-12-31",
        "indicators": ["MA", "RSI"]  # 使用不需要太多数据的指标
    }

    response = requests.post(f"{BASE_URL}/analysis/indicators", json=data)
    logger.info(f"状态码: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        logger.success(f"计算完成，共 {result['count']} 条数据")
        logger.info(f"指标列表: {list(result['indicators'].keys())}")
    else:
        logger.error(f"错误: {response.json()}")


def test_trading_init():
    """测试初始化交易账户"""
    logger.info("\n" + "=" * 80)
    logger.info("测试4: 初始化交易账户")
    logger.info("=" * 80)

    params = {
        "initial_cash": 100000.0,
        "commission_rate": 0.0003,
        "slippage": 0.0001
    }

    response = requests.post(f"{BASE_URL}/trading/init", params=params)
    logger.info(f"状态码: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        logger.success(result['message'])
    else:
        logger.error(f"错误: {response.json()}")


def test_submit_order():
    """测试下单"""
    logger.info("\n" + "=" * 80)
    logger.info("测试5: 提交买单")
    logger.info("=" * 80)

    # 先更新价格
    price_data = {"symbol": "688576.SH", "price": 62.50}
    requests.post(f"{BASE_URL}/trading/price/update", json=price_data)

    # 提交订单
    order_data = {
        "symbol": "688576.SH",
        "action": "BUY",
        "quantity": 100,
        "price": None  # 市价单
    }

    response = requests.post(f"{BASE_URL}/trading/order", json=order_data)
    logger.info(f"状态码: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        logger.success(result['message'])
        logger.info(f"订单ID: {result['order_id']}")
        logger.info(f"成交价格: {result['filled_price']}")
        logger.info(f"手续费: {result['commission']}")
    else:
        logger.error(f"错误: {response.json()}")


def test_get_account():
    """测试获取账户信息"""
    logger.info("\n" + "=" * 80)
    logger.info("测试6: 获取账户信息")
    logger.info("=" * 80)

    response = requests.get(f"{BASE_URL}/trading/account")
    logger.info(f"状态码: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        logger.info(f"现金: {result['cash']:,.2f}")
        logger.info(f"市值: {result['market_value']:,.2f}")
        logger.info(f"总资产: {result['total_value']:,.2f}")
        logger.info(f"持仓数量: {len(result['positions'])}")

        for pos in result['positions']:
            logger.info(f"  {pos['symbol']}: {pos['quantity']}股, "
                       f"盈亏: {pos['unrealized_pnl']:.2f} ({pos['unrealized_pnl_pct']:.2f}%)")
    else:
        logger.error(f"错误: {response.json()}")


def test_backtest():
    """测试回测"""
    logger.info("\n" + "=" * 80)
    logger.info("测试7: 运行回测")
    logger.info("=" * 80)

    data = {
        "strategy_name": "MA_CROSS",
        "symbol": "688576.SH",
        "start_date": "2024-01-01",
        "end_date": "2025-12-31",
        "initial_capital": 1000000.0,
        "strategy_params": {
            "fast_period": 5,
            "slow_period": 20
        }
    }

    response = requests.post(f"{BASE_URL}/backtest/run", json=data)
    logger.info(f"状态码: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        logger.success("回测完成")
        logger.info(f"策略: {result['strategy_name']}")
        logger.info(f"周期: {result['period']}")
        logger.info(f"初始资金: {result['initial_capital']:,.2f}")
        logger.info(f"最终资产: {result['final_value']:,.2f}")
        logger.info(f"总收益: {result['total_return']:,.2f} ({result['total_return_pct']:.2f}%)")
        logger.info(f"夏普比率: {result['sharpe_ratio']:.2f}")
        logger.info(f"最大回撤: {result['max_drawdown_pct']:.2f}%")
        logger.info(f"胜率: {result['win_rate']:.2f}%")
        logger.info(f"总交易次数: {result['total_trades']}")
    else:
        logger.error(f"错误: {response.json()}")


def main():
    """运行所有测试"""
    logger.info("开始测试API接口...")
    logger.info(f"API地址: {BASE_URL}")
    logger.info("=" * 80)

    try:
        # 基础测试
        test_health()
        test_get_data()
        test_calculate_indicators()

        # 交易测试
        test_trading_init()
        test_submit_order()
        test_get_account()

        # 回测测试
        test_backtest()

        logger.info("\n" + "=" * 80)
        logger.success("✓ 所有测试完成！")
        logger.info("=" * 80)

    except requests.exceptions.ConnectionError:
        logger.error("\n❌ 无法连接到API服务器")
        logger.error("请确保API服务器正在运行:")
        logger.error("  bash restart.sh --backend")
    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")


if __name__ == "__main__":
    main()
