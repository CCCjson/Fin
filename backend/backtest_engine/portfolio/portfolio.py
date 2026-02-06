"""
投资组合管理
"""
from typing import Dict, List
from datetime import datetime
from loguru import logger

from .order import Order, OrderStatus
from .position import Position


class Portfolio:
    """投资组合管理器"""

    def __init__(self, initial_capital: float = 100000.0, commission_rate: float = 0.0003):
        """
        初始化投资组合

        Args:
            initial_capital: 初始资金
            commission_rate: 手续费率，默认 0.03%
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_rate = commission_rate

        self.positions: Dict[str, Position] = {}  # 持仓
        self.orders: List[Order] = []  # 订单历史
        self.trades: List[dict] = []  # 交易历史
        self.equity_curve: List[dict] = []  # 权益曲线

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

        # 计算手续费
        commission = abs(order.quantity) * filled_price * self.commission_rate
        total_cost = abs(order.quantity) * filled_price + commission

        # 检查资金
        if order.is_buy and total_cost > self.cash:
            order.status = OrderStatus.REJECTED
            logger.warning(f"资金不足: 需要 {total_cost:.2f}, 可用 {self.cash:.2f}")
            return False

        # 检查持仓（卖出时）
        if order.is_sell:
            position = self.positions.get(order.symbol)
            if not position or position.quantity < abs(order.quantity):
                order.status = OrderStatus.REJECTED
                logger.warning(f"持仓不足: {order.symbol}")
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
