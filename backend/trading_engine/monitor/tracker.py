"""
订单跟踪器
"""
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger

from ..brokers.base import BrokerOrder, OrderStatus


class OrderTracker:
    """订单跟踪器 - 跟踪所有订单状态"""

    def __init__(self):
        self.orders: Dict[str, BrokerOrder] = {}
        self.order_history: List[Dict] = []

    def add_order(self, order: BrokerOrder):
        """添加订单到跟踪系统"""
        self.orders[order.order_id] = order

        self.order_history.append({
            "order_id": order.order_id,
            "symbol": order.symbol,
            "action": order.action,
            "quantity": order.quantity,
            "price": order.price,
            "status": order.status,
            "submit_time": order.submit_time,
            "event": "ORDER_SUBMITTED"
        })

        logger.info(f"订单跟踪: {order.order_id} - {order.symbol} {order.action} {order.quantity}")

    def update_order(self, order: BrokerOrder):
        """更新订单状态"""
        if order.order_id not in self.orders:
            logger.warning(f"订单不存在: {order.order_id}")
            return

        old_status = self.orders[order.order_id].status
        self.orders[order.order_id] = order

        # 记录状态变化
        if old_status != order.status:
            self.order_history.append({
                "order_id": order.order_id,
                "symbol": order.symbol,
                "old_status": old_status,
                "new_status": order.status,
                "update_time": datetime.now(),
                "event": "STATUS_CHANGED"
            })

            logger.info(
                f"订单状态更新: {order.order_id} "
                f"{old_status.value} -> {order.status.value}"
            )

    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """获取订单"""
        return self.orders.get(order_id)

    def get_orders_by_status(self, status: OrderStatus) -> List[BrokerOrder]:
        """按状态获取订单"""
        return [o for o in self.orders.values() if o.status == status]

    def get_orders_by_symbol(self, symbol: str) -> List[BrokerOrder]:
        """按股票代码获取订单"""
        return [o for o in self.orders.values() if o.symbol == symbol]

    def get_pending_orders(self) -> List[BrokerOrder]:
        """获取待处理订单"""
        return self.get_orders_by_status(OrderStatus.PENDING)

    def get_filled_orders(self) -> List[BrokerOrder]:
        """获取已成交订单"""
        return self.get_orders_by_status(OrderStatus.FILLED)

    def get_statistics(self) -> Dict:
        """获取订单统计"""
        total = len(self.orders)
        filled = len(self.get_filled_orders())
        pending = len(self.get_pending_orders())
        rejected = len(self.get_orders_by_status(OrderStatus.REJECTED))
        cancelled = len(self.get_orders_by_status(OrderStatus.CANCELLED))

        return {
            "total_orders": total,
            "filled": filled,
            "pending": pending,
            "rejected": rejected,
            "cancelled": cancelled,
            "fill_rate": (filled / total * 100) if total > 0 else 0
        }

    def get_order_history(self, limit: int = 100) -> List[Dict]:
        """获取订单历史"""
        return self.order_history[-limit:]

    def clear(self):
        """清空跟踪记录"""
        self.orders.clear()
        self.order_history.clear()
        logger.info("订单跟踪已清空")

    def __repr__(self):
        stats = self.get_statistics()
        return (
            f"OrderTracker(总数={stats['total_orders']}, "
            f"已成交={stats['filled']}, "
            f"待处理={stats['pending']})"
        )
