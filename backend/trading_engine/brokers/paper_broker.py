"""
Paper Trading 模拟交易器
"""
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

from .base import BaseBroker, BrokerOrder, BrokerPosition, OrderStatus


# 全局 PaperBroker 单例（进程内共享）——从已删除的 automation/pending_order_manager.py 迁来，
# PaperBroker 的天然归宿。仍被 agents/tools/trading_tools.py（A 股纸面手动下单聊天工具）复用。
_paper_broker: Optional["PaperBroker"] = None


def get_paper_broker() -> "PaperBroker":
    """获取全局 PaperBroker 实例。"""
    global _paper_broker
    if _paper_broker is None:
        from trading_engine.risk.adapter import get_total_capital
        _paper_broker = PaperBroker(initial_cash=get_total_capital())
    return _paper_broker


class PaperBroker(BaseBroker):
    """模拟交易器 - 用于回测和模拟交易"""

    def __init__(
        self,
        initial_cash: float = 1000000.0,
        commission_rate: float = 0.0003,
        slippage: float = 0.0001
    ):
        """
        初始化模拟交易器

        Args:
            initial_cash: 初始资金
            commission_rate: 手续费率，默认 0.03%
            slippage: 滑点，默认 0.01%
        """
        super().__init__(name="PaperBroker")

        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission_rate = commission_rate
        self.stamp_tax_rate = 0.001  # A股卖出印花税 千分之一
        self.slippage = slippage

        # 持仓
        self.positions: Dict[str, BrokerPosition] = {}

        # 订单
        self.orders: Dict[str, BrokerOrder] = {}

        # 市场价格（从外部更新）
        self.market_prices: Dict[str, float] = {}

        # 统计
        self.total_commission = 0.0
        self.total_trades = 0

        logger.info(f"Paper Trading 初始化: 初始资金={initial_cash:,.2f}, 手续费率={commission_rate*100:.2f}%")

    def update_market_price(self, symbol: str, price: float):
        """更新市场价格"""
        self.market_prices[symbol] = price

        # 更新持仓的当前价格
        if symbol in self.positions:
            self.positions[symbol].current_price = price
            pos = self.positions[symbol]
            pos.market_value = pos.quantity * price
            pos.unrealized_pnl = pos.market_value - (pos.quantity * pos.avg_cost)

    def get_account_info(self) -> dict:
        """获取账户信息"""
        total_market_value = sum(pos.market_value for pos in self.positions.values())
        total_value = self.cash + total_market_value
        total_pnl = sum(pos.unrealized_pnl for pos in self.positions.values())

        return {
            "cash": self.cash,
            "market_value": total_market_value,
            "total_value": total_value,
            "unrealized_pnl": total_pnl,
            "total_commission": self.total_commission,
            "total_trades": self.total_trades,
            "initial_cash": self.initial_cash,
            "return_pct": ((total_value - self.initial_cash) / self.initial_cash) * 100,
            "positions": self.positions,
        }

    def get_positions(self) -> List[BrokerPosition]:
        """获取持仓列表"""
        return list(self.positions.values())

    def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        """获取指定持仓"""
        return self.positions.get(symbol)

    def submit_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: Optional[float] = None
    ) -> BrokerOrder:
        """
        提交订单

        Args:
            symbol: 股票代码
            action: BUY 或 SELL
            quantity: 数量
            price: 价格（None=市价单）

        Returns:
            订单对象
        """
        # 生成订单ID
        order_id = str(uuid.uuid4())[:8]

        # 确定成交价格
        if price is None:
            # 市价单：使用当前市场价格
            if symbol not in self.market_prices:
                logger.error(f"未找到 {symbol} 的市场价格")
                return BrokerOrder(
                    order_id=order_id,
                    symbol=symbol,
                    action=action,
                    quantity=quantity,
                    status=OrderStatus.REJECTED,
                    submit_time=datetime.now(),
                    error_msg="未找到市场价格"
                )
            filled_price = self.market_prices[symbol]
        else:
            # 限价单：使用指定价格
            filled_price = price

        # 考虑滑点
        if action == "BUY":
            filled_price *= (1 + self.slippage)
        else:
            filled_price *= (1 - self.slippage)

        # 计算手续费（卖出额外加印花税）
        commission = quantity * filled_price * self.commission_rate
        if action == "SELL":
            commission += quantity * filled_price * self.stamp_tax_rate
        total_cost = quantity * filled_price + commission

        # 创建订单
        order = BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            status=OrderStatus.PENDING,
            submit_time=datetime.now()
        )

        # 验证订单
        if action == "BUY":
            # 买入：检查资金
            if total_cost > self.cash:
                order.status = OrderStatus.REJECTED
                order.error_msg = f"资金不足: 需要 {total_cost:.2f}, 可用 {self.cash:.2f}"
                logger.warning(order.error_msg)
                self.orders[order_id] = order
                return order

        elif action == "SELL":
            # 卖出：检查持仓
            if symbol not in self.positions:
                order.status = OrderStatus.REJECTED
                order.error_msg = f"无持仓: {symbol}"
                logger.warning(order.error_msg)
                self.orders[order_id] = order
                return order

            position = self.positions[symbol]
            if position.available < quantity:
                order.status = OrderStatus.REJECTED
                order.error_msg = f"可用数量不足: 需要 {quantity}, 可用 {position.available}"
                logger.warning(order.error_msg)
                self.orders[order_id] = order
                return order

        # 成交订单
        order.filled_price = filled_price
        order.filled_quantity = quantity
        order.commission = commission
        order.filled_time = datetime.now()
        order.status = OrderStatus.FILLED

        # 更新账户
        if action == "BUY":
            self.cash -= total_cost
            self._add_position(symbol, quantity, filled_price)
            logger.info(f"✓ 买入 {symbol}: {quantity}股 @ {filled_price:.2f}, 手续费={commission:.2f}")

        elif action == "SELL":
            self.cash += (quantity * filled_price - commission)
            self._reduce_position(symbol, quantity, filled_price)
            logger.info(f"✓ 卖出 {symbol}: {quantity}股 @ {filled_price:.2f}, 手续费={commission:.2f}")

        # 统计
        self.total_commission += commission
        self.total_trades += 1

        # 保存订单
        self.orders[order_id] = order

        return order

    def _add_position(self, symbol: str, quantity: int, price: float):
        """增加持仓"""
        if symbol not in self.positions:
            # 新建持仓
            cur_price = self.market_prices.get(symbol, price)
            mkt_value = quantity * cur_price
            self.positions[symbol] = BrokerPosition(
                symbol=symbol,
                quantity=quantity,
                avg_cost=price,
                current_price=cur_price,
                market_value=mkt_value,
                unrealized_pnl=mkt_value - quantity * price,
                # 🔴 T+1 市场（A 股）：当日买入**不可卖**，要等 `settle_t1()` 解冻。
                #    此前这里无条件写 `available=quantity` 并注释「Paper Trading 没有
                #    T+1 限制」—— 那让纸面成绩**系统性优于实盘**（纸面能当天来回做 T），
                #    而「新策略先 paper 跑一段再上实盘」这条护栏正是靠纸面成绩判断的。
                available=0 if self._is_t1(symbol) else quantity,
            )
        else:
            # 增加持仓
            pos = self.positions[symbol]
            total_cost = pos.quantity * pos.avg_cost + quantity * price
            pos.quantity += quantity
            pos.avg_cost = total_cost / pos.quantity
            # T+1 下 available 不动（今天买的这部分仍冻结）；T+0 下跟上总量
            if not self._is_t1(symbol):
                pos.available = pos.quantity
            pos.current_price = self.market_prices.get(symbol, price)
            pos.market_value = pos.quantity * pos.current_price
            pos.unrealized_pnl = pos.market_value - (pos.quantity * pos.avg_cost)

    def _reduce_position(self, symbol: str, quantity: int, price: float):
        """减少持仓"""
        if symbol not in self.positions:
            logger.error(f"持仓不存在: {symbol}")
            return

        pos = self.positions[symbol]
        pos.quantity -= quantity
        # 卖出后可卖量同步减少；T+1 下不能借此把冻结部分「洗」成可卖
        pos.available = min(max(0, pos.available - quantity), pos.quantity)

        if pos.quantity == 0:
            # 清仓
            del self.positions[symbol]
        else:
            # 更新市值和盈亏
            pos.current_price = self.market_prices.get(symbol, price)
            pos.market_value = pos.quantity * pos.current_price
            pos.unrealized_pnl = pos.market_value - (pos.quantity * pos.avg_cost)

    @staticmethod
    def _is_t1(symbol: str) -> bool:
        """这个标的所在市场是不是 T+1。判据走全项目唯一的后缀推断 + 交易规则表。"""
        from common.market import infer_market_from_symbol
        from common.trading_rules import rules_for
        try:
            return rules_for(infer_market_from_symbol(symbol)).t_plus_one
        except KeyError:
            return False   # 认不出的市场按 T+0（宁可宽，也不要凭空冻住卖出）

    def settle_t1(self):
        """新交易日开盘：把昨日及更早买入的持仓解冻为可卖。

        ⚠️ 调用方负责「一天一次」。⛔ 别在 tick 循环里每次都调 ——
        那等于取消了 T+1，纸面成绩会重新变得比实盘好看。
        """
        for pos in self.positions.values():
            pos.available = pos.quantity

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单（模拟交易中订单立即成交，无法撤销）"""
        if order_id not in self.orders:
            logger.warning(f"订单不存在: {order_id}")
            return False

        order = self.orders[order_id]
        if order.status == OrderStatus.FILLED:
            logger.warning(f"订单已成交，无法撤销: {order_id}")
            return False

        order.status = OrderStatus.CANCELLED
        logger.info(f"订单已撤销: {order_id}")
        return True

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """查询订单"""
        return self.orders.get(order_id)

    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """获取订单列表"""
        if symbol:
            return [o for o in self.orders.values() if o.symbol == symbol]
        return list(self.orders.values())

    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        return self.market_prices.get(symbol, 0.0)

    def reset(self):
        """重置账户（用于多次测试）"""
        self.cash = self.initial_cash
        self.positions.clear()
        self.orders.clear()
        self.market_prices.clear()
        self.total_commission = 0.0
        self.total_trades = 0
        logger.info("Paper Trading 账户已重置")
