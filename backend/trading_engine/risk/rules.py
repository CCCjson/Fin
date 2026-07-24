"""
风险规则定义
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from datetime import time

from common.market import A_SHARE, infer_market_from_symbol
from common.market_time import market_now, market_today


def _market_of(kwargs: dict) -> str:
    """从 check() 收到的订单参数推市场（`check_order` 每次都把 symbol 塞进 kwargs）。

    风控是跨市场共用同一套 rule 的：A 股走上海日历、crypto 走 UTC。用错时区会让
    「今天亏损」重置在错误的时刻、「距上次亏损几天」偏 1 天。symbol 缺失时默认 A 股
    （服务器就在上海，与旧的 `datetime.now()` 行为完全一致）。
    """
    symbol = kwargs.get("symbol")
    return infer_market_from_symbol(symbol) if symbol else A_SHARE


@dataclass
class RiskCheckResult:
    """风险检查结果"""
    passed: bool                    # 是否通过
    rule_name: str                  # 规则名称
    message: str                    # 消息
    severity: str = "INFO"          # 严重程度: INFO, WARNING, ERROR

    def __repr__(self):
        emoji = "✓" if self.passed else "✗"
        return f"[{self.severity}] {emoji} {self.rule_name}: {self.message}"


class RiskRule(ABC):
    """风险规则基类"""

    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    def check(self, **kwargs) -> RiskCheckResult:
        """
        执行风险检查

        Returns:
            RiskCheckResult 对象
        """
        pass

    def __repr__(self):
        status = "启用" if self.enabled else "禁用"
        return f"{self.name} ({status})"


class MaxOrderValueRule(RiskRule):
    """单笔订单金额限制"""

    def __init__(self, max_value: float, enabled: bool = True):
        super().__init__("单笔订单金额限制", enabled)
        self.max_value = max_value

    def check(self, quantity: int, price: float, **kwargs) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        order_value = quantity * price

        if order_value > self.max_value:
            return RiskCheckResult(
                False,
                self.name,
                f"订单金额 {order_value:,.2f} 超过限制 {self.max_value:,.2f}",
                "ERROR"
            )

        return RiskCheckResult(
            True,
            self.name,
            f"订单金额 {order_value:,.2f} 在限制内",
            "INFO"
        )


class MaxOrderQuantityRule(RiskRule):
    """单笔订单数量限制"""

    def __init__(self, max_quantity: int, enabled: bool = True):
        super().__init__("单笔订单数量限制", enabled)
        self.max_quantity = max_quantity

    def check(self, quantity: int, **kwargs) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        if quantity > self.max_quantity:
            return RiskCheckResult(
                False,
                self.name,
                f"订单数量 {quantity} 超过限制 {self.max_quantity}",
                "ERROR"
            )

        return RiskCheckResult(
            True,
            self.name,
            f"订单数量 {quantity} 在限制内",
            "INFO"
        )


class MaxPositionValueRule(RiskRule):
    """持仓市值限制"""

    def __init__(self, max_value: float, enabled: bool = True):
        super().__init__("持仓市值限制", enabled)
        self.max_value = max_value

    def check(self, current_market_value: float, order_value: float, action: str, **kwargs) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        # 买入会增加持仓市值
        if action == "BUY":
            new_market_value = current_market_value + order_value

            if new_market_value > self.max_value:
                return RiskCheckResult(
                    False,
                    self.name,
                    f"买入后持仓市值 {new_market_value:,.2f} 将超过限制 {self.max_value:,.2f}",
                    "ERROR"
                )

        return RiskCheckResult(
            True,
            self.name,
            f"持仓市值在限制内",
            "INFO"
        )


class MaxPositionPercentRule(RiskRule):
    """单个品种持仓比例限制"""

    def __init__(self, max_percent: float, enabled: bool = True):
        super().__init__("单品种持仓比例限制", enabled)
        self.max_percent = max_percent  # 0.0 - 1.0

    def check(
        self,
        symbol: str,
        current_position_value: float,
        order_value: float,
        total_value: float,
        action: str,
        **kwargs
    ) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        if action == "BUY":
            new_position_value = current_position_value + order_value
            position_percent = new_position_value / total_value

            if position_percent > self.max_percent:
                return RiskCheckResult(
                    False,
                    self.name,
                    f"{symbol} 持仓比例 {position_percent*100:.2f}% 将超过限制 {self.max_percent*100:.2f}%",
                    "ERROR"
                )

        return RiskCheckResult(
            True,
            self.name,
            f"持仓比例在限制内",
            "INFO"
        )


class MaxDailyLossRule(RiskRule):
    """单日最大亏损限制"""

    def __init__(self, max_daily_loss: float, enabled: bool = True):
        super().__init__("单日最大亏损限制", enabled)
        self.max_daily_loss = max_daily_loss
        self.daily_pnl = 0.0
        # 首次 check 时按当单市场的今天初始化 —— 构造时还不知道 symbol，故不在这里取时间
        self.last_reset_date = None

    def check(self, current_pnl: float = 0.0, **kwargs) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        # 检查是否需要重置（新的一天）—— 「今天」按当单市场算：crypto 在 UTC 午夜重置，
        # A 股在上海午夜。用服务器本地日会让 crypto 在错误的时刻清零当日亏损计数。
        today = market_today(_market_of(kwargs))
        if today != self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = today

        # 更新当日盈亏
        self.daily_pnl = current_pnl

        # 检查是否超过亏损限制
        if self.daily_pnl < -self.max_daily_loss:
            return RiskCheckResult(
                False,
                self.name,
                f"今日亏损 {abs(self.daily_pnl):,.2f} 已达到限制 {self.max_daily_loss:,.2f}，禁止交易",
                "ERROR"
            )

        remaining = self.max_daily_loss + self.daily_pnl
        return RiskCheckResult(
            True,
            self.name,
            f"今日盈亏 {self.daily_pnl:+,.2f}，剩余可亏损 {remaining:,.2f}",
            "INFO"
        )


class TradingHoursRule(RiskRule):
    """交易时间限制"""

    def __init__(self, allowed_hours: list, enabled: bool = True):
        super().__init__("交易时间限制", enabled)
        self.allowed_hours = allowed_hours  # [("09:00", "11:30"), ("13:00", "15:00")]
        # ⚠️ 这条 rule 目前**两条路径都不激活**（RISK_CONFIG 里没有 allow_trading_hours）。
        # 若将来给 crypto 配它，注意 crypto 是 7×24——按下面市场化的 now 判仍会拿 A 股
        # 时段字符串去比，那是配置语义问题，得单独处理，不在时区这一层。

    def check(self, **kwargs) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        # 「现在几点」按当单市场时区，不用服务器本地时钟
        now = market_now(_market_of(kwargs)).time()

        for start_str, end_str in self.allowed_hours:
            start = time.fromisoformat(start_str)
            end = time.fromisoformat(end_str)

            if start <= now <= end:
                return RiskCheckResult(
                    True,
                    self.name,
                    f"当前时间 {now.strftime('%H:%M:%S')} 在交易时间内",
                    "INFO"
                )

        return RiskCheckResult(
            False,
            self.name,
            f"当前时间 {now.strftime('%H:%M:%S')} 不在交易时间内",
            "WARNING"
        )


class MinCashRule(RiskRule):
    """最低现金保留限制"""

    def __init__(self, min_cash: float, enabled: bool = True):
        super().__init__("最低现金保留", enabled)
        self.min_cash = min_cash

    def check(self, current_cash: float, order_value: float, action: str, **kwargs) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        if action == "BUY":
            remaining_cash = current_cash - order_value

            if remaining_cash < self.min_cash:
                return RiskCheckResult(
                    False,
                    self.name,
                    f"买入后剩余现金 {remaining_cash:,.2f} 将低于最低保留 {self.min_cash:,.2f}",
                    "ERROR"
                )

        return RiskCheckResult(
            True,
            self.name,
            f"现金充足",
            "INFO"
        )


class StopLossRule(RiskRule):
    """止损规则"""

    def __init__(self, stop_loss_pct: float, enabled: bool = True):
        super().__init__("止损规则", enabled)
        self.stop_loss_pct = stop_loss_pct  # 止损百分比，如 0.05 表示 5%

    def check(
        self,
        symbol: str,
        avg_cost: float,
        current_price: float,
        **kwargs
    ) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        if avg_cost <= 0:
            return RiskCheckResult(True, self.name, "无持仓成本，跳过", "INFO")

        loss_pct = (current_price - avg_cost) / avg_cost

        if loss_pct < -self.stop_loss_pct:
            return RiskCheckResult(
                False,
                self.name,
                f"{symbol} 亏损 {abs(loss_pct)*100:.2f}% 触发止损（止损线 {self.stop_loss_pct*100:.2f}%）",
                "ERROR"
            )

        return RiskCheckResult(
            True,
            self.name,
            f"未触发止损",
            "INFO"
        )


class TakeProfitRule(RiskRule):
    """止盈规则"""

    def __init__(self, take_profit_pct: float, enabled: bool = True):
        super().__init__("止盈规则", enabled)
        self.take_profit_pct = take_profit_pct  # 止盈百分比

    def check(
        self,
        symbol: str,
        avg_cost: float,
        current_price: float,
        **kwargs
    ) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        if avg_cost <= 0:
            return RiskCheckResult(True, self.name, "无持仓成本，跳过", "INFO")

        profit_pct = (current_price - avg_cost) / avg_cost

        if profit_pct > self.take_profit_pct:
            return RiskCheckResult(
                False,
                self.name,
                f"{symbol} 盈利 {profit_pct*100:.2f}% 触发止盈（止盈线 {self.take_profit_pct*100:.2f}%）",
                "WARNING"  # 止盈通常是建议，不是强制
            )

        return RiskCheckResult(
            True,
            self.name,
            f"未触发止盈",
            "INFO"
        )


class MaxTotalPositionPercentRule(RiskRule):
    """总仓位比例限制（保留现金）"""

    def __init__(self, max_percent: float, enabled: bool = True):
        super().__init__("总仓位比例限制", enabled)
        self.max_percent = max_percent  # 0.0 - 1.0，如 0.8 表示总仓位不超过 80%

    def check(
        self,
        order_value: float,
        current_market_value: float,
        total_value: float,
        action: str,
        **kwargs
    ) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        if action == "BUY":
            new_market_value = current_market_value + order_value
            position_pct = new_market_value / total_value if total_value > 0 else 1.0

            if position_pct > self.max_percent:
                return RiskCheckResult(
                    False,
                    self.name,
                    f"买入后总仓位 {position_pct*100:.1f}% 将超过限制 {self.max_percent*100:.0f}%（需保留 {(1-self.max_percent)*100:.0f}% 现金）",
                    "WARNING"
                )

        return RiskCheckResult(
            True,
            self.name,
            f"总仓位在限制内",
            "INFO"
        )


class ConsecutiveLossRule(RiskRule):
    """连续亏损暂停交易规则"""

    def __init__(self, max_consecutive: int = 3, pause_days: int = 1, enabled: bool = True):
        super().__init__("连续亏损暂停交易", enabled)
        self.max_consecutive = max_consecutive
        self.pause_days = pause_days

    def check(self, recent_closed_pnls: list = None, last_loss_date: str = None, **kwargs) -> RiskCheckResult:
        if not self.enabled:
            return RiskCheckResult(True, self.name, "规则已禁用", "INFO")

        if not recent_closed_pnls:
            return RiskCheckResult(True, self.name, "无平仓记录", "INFO")

        # 从最近的平仓记录中检查连续亏损次数
        consecutive_losses = 0
        for pnl in recent_closed_pnls:
            if pnl < 0:
                consecutive_losses += 1
            else:
                break

        if consecutive_losses >= self.max_consecutive:
            # 检查最后一笔亏损日期，判断是否还在暂停期
            if last_loss_date:
                from datetime import date as date_type
                try:
                    loss_date = date_type.fromisoformat(last_loss_date) if isinstance(last_loss_date, str) else last_loss_date
                    # 「距上次亏损几天」的今天按当单市场算：crypto 用 UTC 日，A 股用上海日。
                    # 用错时区会让暂停期在 crypto 上偏 1 天（凌晨那段尤甚）。
                    days_since = (market_today(_market_of(kwargs)) - loss_date).days
                    if days_since < self.pause_days:
                        return RiskCheckResult(
                            False,
                            self.name,
                            f"已连续亏损 {consecutive_losses} 笔，建议暂停交易 {self.pause_days} 天（距上次亏损仅 {days_since} 天）",
                            "WARNING"
                        )
                except (ValueError, TypeError):
                    pass

            return RiskCheckResult(
                False,
                self.name,
                f"已连续亏损 {consecutive_losses} 笔，建议暂停交易并复盘",
                "WARNING"
            )

        return RiskCheckResult(
            True,
            self.name,
            f"连续亏损 {consecutive_losses} 笔，未触发暂停",
            "INFO"
        )
