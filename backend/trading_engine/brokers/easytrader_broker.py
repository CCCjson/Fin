"""
EasyTrader 实盘券商接口 — 通过模拟操作同花顺/雪球客户端下单

依赖: pip install easytrader
前置条件: 需要运行对应的券商客户端（同花顺/雪球等）
"""
import os
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

from .base import BaseBroker, BrokerOrder, BrokerPosition, OrderStatus


class EasyTraderBroker(BaseBroker):
    """EasyTrader 实盘交易接口"""

    def __init__(self, broker: str = "ths"):
        """
        初始化 EasyTrader

        Args:
            broker: 券商客户端类型 — ths(同花顺), xq(雪球), yh(银河)
        """
        super().__init__(name=f"EasyTrader-{broker}")
        self.broker_type = broker
        self.user = None
        self._connected = False

    def connect(self) -> bool:
        """
        连接券商客户端

        Returns:
            是否连接成功
        """
        try:
            import easytrader
        except ImportError:
            logger.error("easytrader 未安装，请运行: pip install easytrader")
            return False

        try:
            self.user = easytrader.use(self.broker_type)

            # 从环境变量读取配置
            if self.broker_type == "ths":
                exe_path = os.getenv("EASYTRADER_THS_PATH", "")
                if exe_path:
                    self.user.connect(exe_path)
                else:
                    logger.warning("未设置 EASYTRADER_THS_PATH，尝试自动连接")
                    self.user.connect()
            elif self.broker_type == "xq":
                cookies = os.getenv("EASYTRADER_XQ_COOKIES", "")
                if cookies:
                    self.user.prepare(cookies=cookies)
                else:
                    logger.error("雪球模式需要设置 EASYTRADER_XQ_COOKIES")
                    return False
            else:
                self.user.connect()

            self._connected = True
            logger.info(f"EasyTrader 连接成功: {self.broker_type}")
            return True

        except Exception as e:
            logger.error(f"EasyTrader 连接失败: {e}")
            self._connected = False
            return False

    def _ensure_connected(self):
        """确保已连接"""
        if not self._connected or self.user is None:
            raise ConnectionError("EasyTrader 未连接，请先调用 connect()")

    def get_account_info(self) -> dict:
        """获取账户资金信息"""
        self._ensure_connected()
        try:
            balance = self.user.balance
            # easytrader 返回列表，取第一条
            if isinstance(balance, list) and balance:
                b = balance[0]
            elif isinstance(balance, dict):
                b = balance
            else:
                b = {}

            return {
                "cash": b.get("可用金额", b.get("可用余额", 0)),
                "market_value": b.get("股票市值", b.get("证券市值", 0)),
                "total_value": b.get("总资产", 0),
                "frozen": b.get("冻结资金", 0),
            }
        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
            # 键集与正常路径保持一致（含 frozen），杜绝调用方取 ["frozen"] 时 KeyError
            return {"cash": 0, "market_value": 0, "total_value": 0, "frozen": 0}

    def get_positions(self) -> List[BrokerPosition]:
        """获取持仓列表"""
        self._ensure_connected()
        try:
            positions = self.user.position
            result = []
            for pos in positions:
                bp = BrokerPosition(
                    symbol=str(pos.get("证券代码", "")),
                    quantity=int(pos.get("股票余额", pos.get("证券数量", 0))),
                    avg_cost=float(pos.get("成本价", pos.get("买入均价", 0))),
                    current_price=float(pos.get("当前价", pos.get("市价", 0))),
                    market_value=float(pos.get("市值", 0)),
                    unrealized_pnl=float(pos.get("浮动盈亏", pos.get("盈亏", 0))),
                    available=int(pos.get("可用余额", pos.get("可卖数量", 0))),
                )
                result.append(bp)
            return result
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []

    def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        """获取指定股票持仓"""
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol:
                return pos
        return None

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
            symbol: 股票代码（纯数字，如 600519）
            action: BUY 或 SELL
            quantity: 数量（手数 * 100）
            price: 限价（None=市价单）
        """
        self._ensure_connected()
        order_id = str(uuid.uuid4())[:8]

        order = BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            status=OrderStatus.PENDING,
            submit_time=datetime.now()
        )

        try:
            if action == "BUY":
                if price is not None:
                    result = self.user.buy(symbol, price=price, amount=quantity)
                else:
                    result = self.user.market_buy(symbol, amount=quantity)
            elif action == "SELL":
                if price is not None:
                    result = self.user.sell(symbol, price=price, amount=quantity)
                else:
                    result = self.user.market_sell(symbol, amount=quantity)
            else:
                order.status = OrderStatus.REJECTED
                order.error_msg = f"不支持的操作: {action}"
                return order

            # 解析 easytrader 返回
            if isinstance(result, dict):
                entrust_no = result.get("entrust_no", result.get("委托编号", ""))
                if entrust_no:
                    order.order_id = str(entrust_no)
                    order.status = OrderStatus.SUBMITTED
                    order.filled_price = price or 0
                    order.filled_quantity = quantity
                    logger.info(f"EasyTrader 下单成功: {action} {symbol} {quantity}股 @ {price}, 委托号={entrust_no}")
                else:
                    order.status = OrderStatus.REJECTED
                    order.error_msg = str(result)
                    logger.warning(f"EasyTrader 下单失败: {result}")
            else:
                order.status = OrderStatus.SUBMITTED
                order.filled_price = price or 0
                order.filled_quantity = quantity
                logger.info(f"EasyTrader 下单: {action} {symbol} {quantity}股")

        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error_msg = str(e)
            logger.error(f"EasyTrader 下单异常: {e}")

        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        self._ensure_connected()
        try:
            self.user.cancel_entrust(order_id)
            logger.info(f"EasyTrader 撤单成功: {order_id}")
            return True
        except Exception as e:
            logger.error(f"EasyTrader 撤单失败: {e}")
            return False

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """查询订单（从今日委托中查找）"""
        self._ensure_connected()
        try:
            entrusts = self.user.today_entrusts
            for e in entrusts:
                if str(e.get("委托编号", "")) == str(order_id):
                    return BrokerOrder(
                        order_id=str(e["委托编号"]),
                        symbol=str(e.get("证券代码", "")),
                        action="BUY" if "买" in str(e.get("操作", "")) else "SELL",
                        quantity=int(e.get("委托数量", 0)),
                        price=float(e.get("委托价格", 0)),
                        filled_quantity=int(e.get("成交数量", 0)),
                        filled_price=float(e.get("成交均价", 0)),
                        status=OrderStatus.FILLED if int(e.get("成交数量", 0)) > 0 else OrderStatus.SUBMITTED,
                    )
        except Exception as e:
            logger.error(f"查询订单失败: {e}")
        return None

    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """获取今日委托列表"""
        self._ensure_connected()
        try:
            entrusts = self.user.today_entrusts
            orders = []
            for e in entrusts:
                sym = str(e.get("证券代码", ""))
                if symbol and sym != symbol:
                    continue
                orders.append(BrokerOrder(
                    order_id=str(e.get("委托编号", "")),
                    symbol=sym,
                    action="BUY" if "买" in str(e.get("操作", "")) else "SELL",
                    quantity=int(e.get("委托数量", 0)),
                    price=float(e.get("委托价格", 0)),
                    filled_quantity=int(e.get("成交数量", 0)),
                    filled_price=float(e.get("成交均价", 0)),
                    status=OrderStatus.FILLED if int(e.get("成交数量", 0)) > 0 else OrderStatus.SUBMITTED,
                ))
            return orders
        except Exception as e:
            logger.error(f"获取委托列表失败: {e}")
            return []

    def get_current_price(self, symbol: str) -> float:
        """获取当前价格（通过持仓或 easytrader 不直接支持，返回 0）"""
        pos = self.get_position(symbol)
        if pos:
            return pos.current_price
        return 0.0

    @property
    def is_connected(self) -> bool:
        return self._connected
