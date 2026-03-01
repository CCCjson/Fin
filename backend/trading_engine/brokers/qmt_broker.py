"""
QMT (国金证券 miniQMT) 实盘券商接口

通过 HTTP Bridge 与运行在 CrossOver Wine 中的 xtquant 通信。
Bridge Server 运行在 127.0.0.1:5100，本模块作为 HTTP 客户端调用。
"""
import os
import uuid
from datetime import datetime
from typing import List, Optional

import requests
from loguru import logger

from .base import BaseBroker, BrokerOrder, BrokerPosition, OrderStatus


# xtquant order_status 整数 → OrderStatus 映射
_QMT_STATUS_MAP = {
    "SUBMITTED": OrderStatus.SUBMITTED,
    "PARTIAL_FILLED": OrderStatus.PARTIAL_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "FAILED": OrderStatus.FAILED,
    "PENDING": OrderStatus.PENDING,
}


class QMTBroker(BaseBroker):
    """QMT 国金证券实盘交易接口（通过 HTTP Bridge）"""

    def __init__(self, bridge_url: Optional[str] = None, account_id: Optional[str] = None):
        """
        Args:
            bridge_url: Bridge Server 地址，默认从环境变量 QMT_BRIDGE_URL 读取
            account_id: 国金资金账号，默认从环境变量 QMT_ACCOUNT_ID 读取
        """
        super().__init__(name="QMT-国金证券")
        self.bridge_url = (bridge_url or os.getenv("QMT_BRIDGE_URL", "http://127.0.0.1:5100")).rstrip("/")
        self.account_id = account_id or os.getenv("QMT_ACCOUNT_ID", "")
        self._connected = False
        self._timeout = 10  # HTTP 请求超时秒数

    def connect(self) -> bool:
        """
        连接 QMT Bridge

        先检查 Bridge 是否在线（GET /health），
        如果 Bridge 在线但 QMT 未连接，则尝试 POST /connect。
        """
        try:
            resp = self._request("GET", "/health")
            if resp is None:
                logger.error("QMT Bridge 不在线，请先启动 bridge_server.py")
                return False

            if resp.get("qmt_connected"):
                self._connected = True
                logger.info(f"QMT Bridge 已连接，账户: {resp.get('account_id', 'N/A')}")
                return True

            # Bridge 在线但 QMT 未连接，尝试连接
            connect_resp = self._request("POST", "/connect", json={
                "account_id": self.account_id,
                "mini_path": os.getenv("QMT_MINI_PATH", ""),
            })
            if connect_resp and connect_resp.get("success"):
                self._connected = True
                logger.info("QMT 连接成功")
                return True
            else:
                error = connect_resp.get("error", "未知错误") if connect_resp else "无响应"
                logger.error(f"QMT 连接失败: {error}")
                return False

        except Exception as e:
            logger.error(f"QMT 连接异常: {e}")
            return False

    def _ensure_connected(self):
        """确保已连接"""
        if not self._connected:
            raise ConnectionError("QMT 未连接，请先调用 connect()")

    def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        """
        统一 HTTP 请求方法

        Args:
            method: GET/POST
            path: API 路径（如 /health）
            **kwargs: 传给 requests 的参数

        Returns:
            JSON 响应字典，失败返回 None
        """
        url = f"{self.bridge_url}{path}"
        kwargs.setdefault("timeout", self._timeout)
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.ConnectionError:
            logger.error(f"QMT Bridge 连接失败: {url}")
            self._connected = False
            return None
        except requests.Timeout:
            logger.error(f"QMT Bridge 请求超时: {url}")
            return None
        except Exception as e:
            logger.error(f"QMT Bridge 请求异常: {url} | {e}")
            return None

    def get_account_info(self) -> dict:
        """获取账户资金信息"""
        self._ensure_connected()
        resp = self._request("GET", "/account")
        if not resp or not resp.get("success"):
            error = resp.get("error", "未知错误") if resp else "无响应"
            logger.error(f"获取账户信息失败: {error}")
            return {"cash": 0, "market_value": 0, "total_value": 0}

        data = resp.get("data", {})
        return {
            "cash": data.get("cash", 0),
            "market_value": data.get("market_value", 0),
            "total_value": data.get("total_value", 0),
            "frozen": data.get("frozen", 0),
        }

    def get_positions(self) -> List[BrokerPosition]:
        """获取持仓列表"""
        self._ensure_connected()
        resp = self._request("GET", "/positions")
        if not resp or not resp.get("success"):
            error = resp.get("error", "未知错误") if resp else "无响应"
            logger.error(f"获取持仓失败: {error}")
            return []

        result = []
        for pos in resp.get("data", []):
            bp = BrokerPosition(
                symbol=pos.get("symbol", ""),
                quantity=int(pos.get("quantity", 0)),
                avg_cost=float(pos.get("avg_cost", 0)),
                current_price=float(pos.get("current_price", 0)),
                market_value=float(pos.get("market_value", 0)),
                unrealized_pnl=float(pos.get("unrealized_pnl", 0)),
                available=int(pos.get("available", 0)),
            )
            result.append(bp)
        return result

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
        price: Optional[float] = None,
    ) -> BrokerOrder:
        """
        提交订单

        Args:
            symbol: 股票代码（如 600519 或 000858）
            action: BUY 或 SELL
            quantity: 数量
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
            submit_time=datetime.now(),
        )

        resp = self._request("POST", "/order", json={
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "price": price,
        })

        if not resp:
            order.status = OrderStatus.FAILED
            order.error_msg = "Bridge 无响应"
            return order

        if resp.get("success"):
            order.order_id = str(resp.get("order_id", order_id))
            order.status = OrderStatus.SUBMITTED
            order.filled_price = price or 0
            order.filled_quantity = quantity
            logger.info(f"QMT 下单成功: {action} {symbol} {quantity}股 @ {price}, 委托号={order.order_id}")
        else:
            order.status = OrderStatus.FAILED
            order.error_msg = resp.get("error", "下单失败")
            logger.warning(f"QMT 下单失败: {order.error_msg}")

        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        self._ensure_connected()
        resp = self._request("POST", "/cancel", json={"order_id": order_id})
        if resp and resp.get("success"):
            logger.info(f"QMT 撤单成功: {order_id}")
            return True
        error = resp.get("error", "未知错误") if resp else "无响应"
        logger.error(f"QMT 撤单失败: {error}")
        return False

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """查询指定订单"""
        orders = self.get_orders()
        for o in orders:
            if o.order_id == str(order_id):
                return o
        return None

    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """获取委托列表"""
        self._ensure_connected()
        params = {}
        if symbol:
            params["symbol"] = symbol
        resp = self._request("GET", "/orders", params=params)
        if not resp or not resp.get("success"):
            error = resp.get("error", "未知错误") if resp else "无响应"
            logger.error(f"获取委托列表失败: {error}")
            return []

        result = []
        for o in resp.get("data", []):
            status_str = o.get("status", "PENDING")
            status = _QMT_STATUS_MAP.get(status_str, OrderStatus.PENDING)
            result.append(BrokerOrder(
                order_id=str(o.get("order_id", "")),
                symbol=o.get("symbol", ""),
                action=o.get("action", ""),
                quantity=int(o.get("quantity", 0)),
                price=float(o.get("price", 0)) if o.get("price") else None,
                filled_quantity=int(o.get("filled_quantity", 0)),
                filled_price=float(o.get("filled_price", 0)),
                status=status,
            ))
        return result

    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        self._ensure_connected()
        resp = self._request("GET", f"/price/{symbol}")
        if resp and resp.get("success"):
            return float(resp.get("price", 0))
        return 0.0

    @property
    def is_connected(self) -> bool:
        return self._connected
