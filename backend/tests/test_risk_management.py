"""
测试风险管理模块
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

from trading_engine.risk import RiskManager
from trading_engine.brokers.paper_broker import PaperBroker
from trading_engine.config import RISK_CONFIG


def main():
    logger.info("\n" + "=" * 80)
    logger.info("测试风险管理模块")
    logger.info("=" * 80 + "\n")

    # 创建风险管理器
    logger.info("【步骤1】初始化风险管理器...")
    risk_manager = RiskManager(RISK_CONFIG)
    logger.info(risk_manager.get_rules_summary())
    logger.success("✓ 风险管理器初始化完成\n")

    # 创建模拟交易器
    logger.info("【步骤2】创建模拟交易账户...")
    broker = PaperBroker(initial_cash=100000.0)
    broker.update_market_price("688576.SH", 60.0)
    logger.success("✓ 账户创建完成\n")

    # 测试场景
    logger.info("【步骤3】测试风险检查场景...")
    logger.info("=" * 80)

    # 场景1: 正常订单
    logger.info("\n场景1: 正常订单（应该通过）")
    logger.info("-" * 80)
    broker_info = broker.get_account_info()
    broker_info["positions"] = {s: broker.get_position(s) for s in ["688576.SH"] if broker.get_position(s)}

    passed, results = risk_manager.check_order(
        symbol="688576.SH",
        action="BUY",
        quantity=500,
        price=60.0,
        broker_info=broker_info
    )

    for result in results:
        if result.passed:
            logger.info(f"  ✓ {result.rule_name}: {result.message}")
        else:
            logger.error(f"  ✗ {result.rule_name}: {result.message}")

    if passed:
        logger.success("\n✓ 订单通过风险检查")
    else:
        logger.error("\n✗ 订单未通过风险检查")

    # 场景2: 超过单笔金额限制
    logger.info("\n\n场景2: 超过单笔金额限制（应该拒绝）")
    logger.info("-" * 80)

    passed, results = risk_manager.check_order(
        symbol="688576.SH",
        action="BUY",
        quantity=1000,
        price=100.0,  # 100,000 元，超过 50,000 限制
        broker_info=broker_info
    )

    for result in results:
        if result.passed:
            logger.info(f"  ✓ {result.rule_name}: {result.message}")
        else:
            logger.error(f"  ✗ {result.rule_name}: {result.message}")

    if passed:
        logger.warning("\n⚠️  订单通过（预期应该拒绝）")
    else:
        logger.success("\n✓ 订单被正确拒绝")

    # 场景3: 超过数量限制
    logger.info("\n\n场景3: 超过单笔数量限制（应该拒绝）")
    logger.info("-" * 80)

    passed, results = risk_manager.check_order(
        symbol="688576.SH",
        action="BUY",
        quantity=1500,  # 超过 1000 限制
        price=30.0,
        broker_info=broker_info
    )

    for result in results:
        if result.passed:
            logger.info(f"  ✓ {result.rule_name}: {result.message}")
        else:
            logger.error(f"  ✗ {result.rule_name}: {result.message}")

    if passed:
        logger.warning("\n⚠️  订单通过（预期应该拒绝）")
    else:
        logger.success("\n✓ 订单被正确拒绝")

    # 场景4: 资金不足
    logger.info("\n\n场景4: 资金不足（应该拒绝）")
    logger.info("-" * 80)

    passed, results = risk_manager.check_order(
        symbol="688576.SH",
        action="BUY",
        quantity=800,
        price=150.0,  # 120,000 元，超过可用资金
        broker_info=broker_info
    )

    for result in results:
        if result.passed:
            logger.info(f"  ✓ {result.rule_name}: {result.message}")
        else:
            logger.error(f"  ✗ {result.rule_name}: {result.message}")

    if passed:
        logger.warning("\n⚠️  订单通过（预期应该拒绝）")
    else:
        logger.success("\n✓ 订单被正确拒绝")

    # 场景5: 测试止损
    logger.info("\n\n场景5: 测试止损检查")
    logger.info("-" * 80)

    # 先买入建仓
    broker.submit_order("688576.SH", "BUY", 500, 60.0)
    logger.info("建仓: 500股 @ 60.0")

    # 模拟价格下跌触发止损（假设止损线 5%）
    broker.update_market_price("688576.SH", 56.0)  # 下跌 6.67%
    logger.info("价格下跌到: 56.0")

    position = broker.get_position("688576.SH")
    should_close, results = risk_manager.check_position(
        symbol="688576.SH",
        position_info={
            "avg_cost": position.avg_cost,
            "quantity": position.quantity,
        },
        current_price=56.0
    )

    for result in results:
        if result.passed:
            logger.info(f"  ✓ {result.rule_name}: {result.message}")
        else:
            logger.warning(f"  ⚠️ {result.rule_name}: {result.message}")

    if should_close:
        logger.warning("\n⚠️  建议平仓（触发风险规则）")
    else:
        logger.info("\n✓ 持仓正常")

    # 场景6: 测试止盈
    logger.info("\n\n场景6: 测试止盈检查")
    logger.info("-" * 80)

    # 模拟价格上涨触发止盈（假设止盈线 10%）
    broker.update_market_price("688576.SH", 67.0)  # 上涨 11.67%
    logger.info("价格上涨到: 67.0")

    should_close, results = risk_manager.check_position(
        symbol="688576.SH",
        position_info={
            "avg_cost": position.avg_cost,
            "quantity": position.quantity,
        },
        current_price=67.0
    )

    for result in results:
        if result.passed:
            logger.info(f"  ✓ {result.rule_name}: {result.message}")
        else:
            logger.warning(f"  ⚠️ {result.rule_name}: {result.message}")

    if should_close:
        logger.warning("\n⚠️  建议平仓（触发止盈）")
    else:
        logger.info("\n✓ 持仓正常")

    # 规则管理
    logger.info("\n\n【步骤4】测试规则管理...")
    logger.info("=" * 80)

    logger.info("\n禁用单笔订单金额限制...")
    risk_manager.disable_rule("单笔订单金额限制")

    logger.info("\n重新检查之前被拒绝的大额订单:")
    passed, results = risk_manager.check_order(
        symbol="688576.SH",
        action="BUY",
        quantity=1000,
        price=100.0,
        broker_info=broker_info
    )

    critical_failures = [r for r in results if not r.passed and r.severity == "ERROR"]
    if not critical_failures:
        logger.success("✓ 订单通过（金额限制已禁用）")
    else:
        logger.warning(f"✗ 订单仍被拒绝: {critical_failures}")

    # 最终总结
    logger.info("\n" + "=" * 80)
    logger.info("【测试总结】")
    logger.info("=" * 80)
    logger.info(f"\n{risk_manager}")
    logger.info(risk_manager.get_rules_summary())

    logger.info("\n" + "=" * 80)
    logger.success("🎉 风险管理模块测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
