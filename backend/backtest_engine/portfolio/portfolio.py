"""
投资组合管理

费用模型现已按市场区分（A股/港股/美股），不再是 A 股专属：
默认构造参数仍是 A 股配置（向后兼容旧调用方），但 `Portfolio.for_market()`
会按 symbol 后缀推断出的市场自动套用对应费率预设，供 backtest_engine 使用。
"""
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger

from .order import Order, OrderStatus
from .position import Position


def infer_market(symbol: Optional[str]) -> str:
    """symbol 后缀推断市场（委托 common.market 单一真源，保留本名供既有调用方）。"""
    from common.market import infer_market_from_symbol
    return infer_market_from_symbol(symbol)


# 各市场费用预设：commission_rate=佣金率, min_commission=最低佣金(元/笔),
# stamp_tax=印花税率, stamp_tax_sell_only=印花税是否仅卖出收取, slippage_pct=滑点比例
_MARKET_FEES: Dict[str, Dict[str, float]] = {
    "a_share": {
        "commission_rate": 0.00025,       # 佣金万 2.5
        "min_commission": 5.0,            # 最低佣金 5 元/笔（A 股券商规则）
        "stamp_tax": 0.001,               # 印花税千 1
        "stamp_tax_sell_only": True,      # 仅卖出收取
        "slippage_pct": 0.001,            # 滑点 0.1%
    },
    "hk_stock": {
        "commission_rate": 0.0005,        # 佣金万 5
        "min_commission": 0.0,            # 港股无最低佣金硬性下限
        "stamp_tax": 0.001,               # 印花税千 1，买卖双边收取
        "stamp_tax_sell_only": False,
        "slippage_pct": 0.001,
    },
    "us_stock": {
        # 与 C++ 回测口径对齐(backtest_cpp types.h::us_stock)：有佣金券商
        "commission_rate": 0.0001,        # 佣金万 1
        "min_commission": 1.0,            # 最低佣金 1 美元/笔
        "stamp_tax": 0.0,                 # 美股无印花税
        "stamp_tax_sell_only": False,
        "slippage_pct": 0.0005,           # 滑点 0.05%
    },
}


class Portfolio:
    """投资组合管理器"""

    def __init__(
        self,
        initial_capital: float = 100000.0,
        commission_rate: float = 0.00025,     # 佣金率，默认 A 股万 2.5
        min_commission: float = 5.0,          # 最低佣金 5 元/笔（A 股券商规则）
        stamp_tax: float = 0.001,             # 印花税率 千 1
        stamp_tax_sell_only: bool = True,     # 印花税仅卖出收取
        slippage_pct: float = 0.001,          # 滑点 0.1%（与 backtest_cpp a_share 一致）
    ):
        """
        初始化投资组合（构造参数默认值为 A 股配置，向后兼容旧调用方；
        若需按市场自动套用费率预设，请用 `Portfolio.for_market()`）

        Args:
            initial_capital: 初始资金
            commission_rate: 佣金率（默认 A 股万 2.5）
            min_commission: 最低佣金（元/笔）
            stamp_tax: 印花税率
            stamp_tax_sell_only: 印花税是否仅卖出收取
            slippage_pct: 滑点比例（买入价偏高、卖出价偏低）
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_tax = stamp_tax
        self.stamp_tax_sell_only = stamp_tax_sell_only
        self.slippage_pct = slippage_pct

        self.positions: Dict[str, Position] = {}  # 持仓
        self.orders: List[Order] = []  # 订单历史
        self.trades: List[dict] = []  # 交易历史
        self.equity_curve: List[dict] = []  # 权益曲线

    @classmethod
    def for_market(cls, market: str, initial_capital: float = 100000.0) -> "Portfolio":
        """按市场费率预设构造 Portfolio（market 未知时兜底用 a_share 预设）。

        Args:
            market: 市场标识，"a_share" / "hk_stock" / "us_stock"（见 infer_market）
            initial_capital: 初始资金

        Returns:
            套用对应市场费率预设的 Portfolio 实例
        """
        fees = _MARKET_FEES.get(market, _MARKET_FEES["a_share"])
        return cls(initial_capital=initial_capital, **fees)

    @property
    def market_value(self) -> float:
        """持仓市值"""
        return sum(pos.market_value for pos in self.positions.values())

    @property
    def total_value(self) -> float:
        """总资产 = 现金 + 持仓市值"""
        return self.cash + self.market_value

    @property
    def total_return(self) -> float:
        """总收益"""
        return self.total_value - self.initial_capital

    @property
    def total_return_pct(self) -> float:
        """总收益率"""
        return (self.total_return / self.initial_capital) * 100

    def update_prices(self, prices: Dict[str, float]):
        """
        更新持仓价格

        Args:
            prices: 股票代码 -> 价格的字典
        """
        for symbol, position in self.positions.items():
            if symbol in prices:
                position.update_price(prices[symbol])

    def settle_t1(self):
        """T+1 结算：把所有持仓的可卖数量（available）解冻为当前持有量。

        每个交易日开盘时调用一次；由于买入时只加 quantity 不加 available，
        当日买入的股票要到下一交易日 settle_t1() 才计入可卖，从而实现 A 股 T+1。
        """
        for position in self.positions.values():
            position.available = position.quantity

    def process_order(self, order: Order, current_price: float) -> bool:
        """
        处理订单

        Args:
            order: 订单
            current_price: 当前价格

        Returns:
            是否成交
        """
        # 确定成交价格
        if order.order_type.value == "market":
            filled_price = current_price
        else:  # 限价单
            if order.is_buy and current_price > order.price:
                return False  # 价格太高，不成交
            if order.is_sell and current_price < order.price:
                return False  # 价格太低，不成交
            filled_price = order.price

        # 应用滑点：买入价偏高，卖出价偏低
        if self.slippage_pct > 0:
            filled_price *= (1 + self.slippage_pct) if order.is_buy else (1 - self.slippage_pct)

        # 计算手续费：佣金(不低于最低) + 印花税(默认仅卖出)
        amount = abs(order.quantity) * filled_price
        commission = max(amount * self.commission_rate, self.min_commission)
        if order.is_sell or not self.stamp_tax_sell_only:
            commission += amount * self.stamp_tax
        total_cost = amount + commission

        # 检查资金
        if order.is_buy and total_cost > self.cash:
            order.status = OrderStatus.REJECTED
            logger.warning(f"资金不足: 需要 {total_cost:.2f}, 可用 {self.cash:.2f}")
            return False

        # 检查持仓（卖出时）—— T+1：只能卖出"可卖数量"available（当日买入被冻结）
        if order.is_sell:
            position = self.positions.get(order.symbol)
            if not position or position.available < abs(order.quantity):
                order.status = OrderStatus.REJECTED
                logger.warning(f"可卖持仓不足(T+1): {order.symbol}")
                return False

        # 成交
        order.filled_price = filled_price
        order.filled_time = order.timestamp
        order.commission = commission
        order.status = OrderStatus.FILLED

        # 更新现金
        if order.is_buy:
            self.cash -= total_cost
        else:
            self.cash += (abs(order.quantity) * filled_price - commission)

        # 更新持仓
        self._update_position(order)

        # 记录交易
        self.trades.append({
            "timestamp": order.filled_time,
            "symbol": order.symbol,
            "action": "BUY" if order.is_buy else "SELL",
            "quantity": abs(order.quantity),
            "price": filled_price,
            "commission": commission,
            "cash": self.cash,
            "total_value": self.total_value
        })

        self.orders.append(order)
        return True

    def _update_position(self, order: Order):
        """更新持仓"""
        symbol = order.symbol

        # 创建或获取持仓
        if symbol not in self.positions:
            self.positions[symbol] = Position(
                symbol=symbol,
                current_price=order.filled_price
            )

        position = self.positions[symbol]

        # 添加交易记录
        position.add_trade(
            quantity=order.quantity,
            price=order.filled_price,
            timestamp=order.filled_time,
            commission=order.commission
        )

        # 如果清仓，删除持仓记录
        if position.quantity == 0:
            del self.positions[symbol]

    def record_equity(self, timestamp: datetime):
        """记录权益曲线"""
        self.equity_curve.append({
            "timestamp": timestamp,
            "cash": self.cash,
            "market_value": self.market_value,
            "total_value": self.total_value,
            "return": self.total_return,
            "return_pct": self.total_return_pct
        })

    def get_position(self, symbol: str) -> Position:
        """获取持仓"""
        return self.positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        """是否持有仓位"""
        return symbol in self.positions and self.positions[symbol].quantity > 0

    def get_summary(self) -> dict:
        """获取组合摘要"""
        return {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "market_value": self.market_value,
            "total_value": self.total_value,
            "total_return": self.total_return,
            "total_return_pct": self.total_return_pct,
            "num_positions": len(self.positions),
            "num_trades": len(self.trades),
            "num_orders": len(self.orders)
        }

    def __repr__(self):
        return (
            f"Portfolio(cash={self.cash:.2f}, "
            f"market_value={self.market_value:.2f}, "
            f"total={self.total_value:.2f}, "
            f"return={self.total_return_pct:.2f}%)"
        )
