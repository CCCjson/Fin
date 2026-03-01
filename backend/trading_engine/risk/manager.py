"""
风险管理器
"""
from typing import List, Dict, Optional
from loguru import logger

from .rules import (
    RiskRule,
    RiskCheckResult,
    MaxOrderValueRule,
    MaxOrderQuantityRule,
    MaxPositionValueRule,
    MaxPositionPercentRule,
    MaxDailyLossRule,
    TradingHoursRule,
    MinCashRule,
    StopLossRule,
    TakeProfitRule,
    MaxTotalPositionPercentRule,
    ConsecutiveLossRule,
)


class RiskManager:
    """风险管理器"""

    def __init__(self, config: Dict = None):
        """
        初始化风险管理器

        Args:
            config: 风险配置字典
        """
        self.rules: List[RiskRule] = []
        self.config = config or {}

        # 从配置加载规则
        self._load_rules_from_config()

        logger.info(f"风险管理器初始化: 加载 {len(self.rules)} 条规则")

    def _load_rules_from_config(self):
        """从配置加载风险规则"""
        # 单笔订单限制
        if "max_order_value" in self.config:
            self.add_rule(MaxOrderValueRule(self.config["max_order_value"]))

        if "max_order_quantity" in self.config:
            self.add_rule(MaxOrderQuantityRule(self.config["max_order_quantity"]))

        # 持仓限制
        if "max_position_value" in self.config:
            self.add_rule(MaxPositionValueRule(self.config["max_position_value"]))

        if "max_position_pct" in self.config:
            self.add_rule(MaxPositionPercentRule(self.config["max_position_pct"]))

        # 损失限制（支持绝对值或百分比）
        if "max_daily_loss" in self.config:
            self.add_rule(MaxDailyLossRule(self.config["max_daily_loss"]))
        elif "max_daily_loss_pct" in self.config:
            from trading_engine.risk.adapter import get_total_capital
            capital = get_total_capital()
            self.add_rule(MaxDailyLossRule(capital * self.config["max_daily_loss_pct"]))

        # 交易时间
        if "allow_trading_hours" in self.config:
            self.add_rule(TradingHoursRule(self.config["allow_trading_hours"]))

        # 最低现金
        if "min_cash" in self.config:
            self.add_rule(MinCashRule(self.config["min_cash"]))

        # 止损止盈
        if "stop_loss_pct" in self.config:
            self.add_rule(StopLossRule(self.config["stop_loss_pct"]))

        if "take_profit_pct" in self.config:
            self.add_rule(TakeProfitRule(self.config["take_profit_pct"]))

        # 总仓位限制
        if "max_total_position_pct" in self.config:
            self.add_rule(MaxTotalPositionPercentRule(self.config["max_total_position_pct"]))

        # 连续亏损暂停
        if "max_consecutive_losses" in self.config:
            self.add_rule(ConsecutiveLossRule(
                max_consecutive=self.config["max_consecutive_losses"],
                pause_days=self.config.get("consecutive_loss_pause_days", 1),
            ))

    def add_rule(self, rule: RiskRule):
        """添加风险规则"""
        self.rules.append(rule)
        logger.debug(f"添加风险规则: {rule}")

    def remove_rule(self, rule_name: str):
        """移除风险规则"""
        self.rules = [r for r in self.rules if r.name != rule_name]
        logger.debug(f"移除风险规则: {rule_name}")

    def enable_rule(self, rule_name: str):
        """启用规则"""
        for rule in self.rules:
            if rule.name == rule_name:
                rule.enabled = True
                logger.info(f"启用风险规则: {rule_name}")
                return True
        return False

    def disable_rule(self, rule_name: str):
        """禁用规则"""
        for rule in self.rules:
            if rule.name == rule_name:
                rule.enabled = False
                logger.info(f"禁用风险规则: {rule_name}")
                return True
        return False

    def check_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        broker_info: Dict
    ) -> tuple[bool, List[RiskCheckResult]]:
        """
        检查订单是否符合风险规则

        Args:
            symbol: 股票代码
            action: BUY 或 SELL
            quantity: 数量
            price: 价格
            broker_info: 券商账户信息

        Returns:
            (是否通过, 检查结果列表)
        """
        results = []
        all_passed = True

        order_value = quantity * price

        # 执行所有规则检查
        for rule in self.rules:
            if not rule.enabled:
                continue

            try:
                # 准备参数
                kwargs = {
                    "symbol": symbol,
                    "action": action,
                    "quantity": quantity,
                    "price": price,
                    "order_value": order_value,
                    "current_cash": broker_info.get("cash", 0),
                    "current_market_value": broker_info.get("market_value", 0),
                    "total_value": broker_info.get("total_value", 0),
                    "current_pnl": broker_info.get("unrealized_pnl", 0),
                    "recent_closed_pnls": broker_info.get("recent_closed_pnls", []),
                    "last_loss_date": broker_info.get("last_loss_date"),
                }

                # 如果是针对特定持仓的检查，添加持仓信息
                if "positions" in broker_info and symbol in broker_info["positions"]:
                    pos = broker_info["positions"][symbol]
                    # 支持字典和对象两种访问方式
                    if isinstance(pos, dict):
                        kwargs.update({
                            "current_position_value": pos.get("market_value", 0) or 0,
                            "avg_cost": pos.get("avg_cost", 0),
                            "current_price": pos.get("current_price", price),
                        })
                    else:
                        kwargs.update({
                            "current_position_value": pos.market_value,
                            "avg_cost": pos.avg_cost,
                            "current_price": pos.current_price,
                        })
                else:
                    kwargs.update({
                        "current_position_value": 0,
                        "avg_cost": 0,
                        "current_price": price,
                    })

                result = rule.check(**kwargs)
                results.append(result)

                if not result.passed:
                    all_passed = False
                    if result.severity == "ERROR":
                        logger.error(f"风险检查失败: {result}")
                    elif result.severity == "WARNING":
                        logger.warning(f"风险警告: {result}")

            except Exception as e:
                logger.error(f"风险规则 {rule.name} 执行失败: {e}")
                result = RiskCheckResult(
                    False,
                    rule.name,
                    f"规则执行异常: {e}",
                    "ERROR"
                )
                results.append(result)
                all_passed = False

        return all_passed, results

    def check_position(
        self,
        symbol: str,
        position_info: Dict,
        current_price: float
    ) -> tuple[bool, List[RiskCheckResult]]:
        """
        检查持仓是否需要止损止盈

        Args:
            symbol: 股票代码
            position_info: 持仓信息
            current_price: 当前价格

        Returns:
            (是否需要平仓, 检查结果列表)
        """
        results = []
        should_close = False

        for rule in self.rules:
            if not rule.enabled:
                continue

            # 只检查止损止盈规则
            if isinstance(rule, (StopLossRule, TakeProfitRule)):
                try:
                    result = rule.check(
                        symbol=symbol,
                        avg_cost=position_info.get("avg_cost", 0),
                        current_price=current_price
                    )
                    results.append(result)

                    if not result.passed:
                        should_close = True
                        logger.warning(f"持仓检查: {result}")

                except Exception as e:
                    logger.error(f"持仓检查规则 {rule.name} 执行失败: {e}")

        return should_close, results

    def get_rules_summary(self) -> str:
        """获取规则摘要"""
        lines = ["风险管理规则:"]
        for rule in self.rules:
            status = "✓" if rule.enabled else "✗"
            lines.append(f"  {status} {rule.name}")
        return "\n".join(lines)

    def __repr__(self):
        enabled_count = sum(1 for r in self.rules if r.enabled)
        return f"RiskManager(规则总数={len(self.rules)}, 已启用={enabled_count})"
