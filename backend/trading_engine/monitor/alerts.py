"""
告警管理器
"""
from enum import Enum
from typing import List, Dict, Callable
from datetime import datetime
from loguru import logger


class AlertType(Enum):
    """告警类型"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Alert:
    """告警"""

    def __init__(
        self,
        alert_type: AlertType,
        title: str,
        message: str,
        data: Dict = None
    ):
        self.alert_type = alert_type
        self.title = title
        self.message = message
        self.data = data or {}
        self.timestamp = datetime.now()
        self.acknowledged = False

    def acknowledge(self):
        """确认告警"""
        self.acknowledged = True

    def __repr__(self):
        emoji = {
            AlertType.INFO: "ℹ️",
            AlertType.WARNING: "⚠️",
            AlertType.ERROR: "❌",
            AlertType.CRITICAL: "🚨"
        }.get(self.alert_type, "")

        return (
            f"{emoji} [{self.alert_type.value.upper()}] {self.title}\n"
            f"   {self.message}\n"
            f"   时间: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        )


class AlertManager:
    """告警管理器"""

    def __init__(self):
        self.alerts: List[Alert] = []
        self.handlers: Dict[AlertType, List[Callable]] = {
            AlertType.INFO: [],
            AlertType.WARNING: [],
            AlertType.ERROR: [],
            AlertType.CRITICAL: []
        }

    def add_handler(self, alert_type: AlertType, handler: Callable):
        """
        添加告警处理器

        Args:
            alert_type: 告警类型
            handler: 处理函数，接收 Alert 对象作为参数
        """
        self.handlers[alert_type].append(handler)
        logger.debug(f"添加告警处理器: {alert_type.value}")

    def send_alert(
        self,
        alert_type: AlertType,
        title: str,
        message: str,
        data: Dict = None
    ):
        """发送告警"""
        alert = Alert(alert_type, title, message, data)
        self.alerts.append(alert)

        # 记录日志
        log_method = {
            AlertType.INFO: logger.info,
            AlertType.WARNING: logger.warning,
            AlertType.ERROR: logger.error,
            AlertType.CRITICAL: logger.critical
        }.get(alert_type, logger.info)

        log_method(f"告警: {alert.title} - {alert.message}")

        # 调用处理器
        for handler in self.handlers[alert_type]:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"告警处理器执行失败: {e}")

    def send_risk_alert(self, rule_name: str, message: str, severity: str):
        """发送风险告警"""
        alert_type = {
            "INFO": AlertType.INFO,
            "WARNING": AlertType.WARNING,
            "ERROR": AlertType.ERROR
        }.get(severity, AlertType.WARNING)

        self.send_alert(
            alert_type,
            f"风险告警: {rule_name}",
            message,
            {"rule_name": rule_name}
        )

    def send_position_alert(self, symbol: str, message: str, alert_type: AlertType = AlertType.WARNING):
        """发送持仓告警"""
        self.send_alert(
            alert_type,
            f"持仓告警: {symbol}",
            message,
            {"symbol": symbol}
        )

    def send_order_alert(self, order_id: str, message: str, alert_type: AlertType = AlertType.WARNING):
        """发送订单告警"""
        self.send_alert(
            alert_type,
            f"订单告警: {order_id}",
            message,
            {"order_id": order_id}
        )

    def send_system_alert(self, message: str, alert_type: AlertType = AlertType.ERROR):
        """发送系统告警"""
        self.send_alert(
            alert_type,
            "系统告警",
            message
        )

    def get_alerts(
        self,
        alert_type: AlertType = None,
        acknowledged: bool = None,
        limit: int = 100
    ) -> List[Alert]:
        """获取告警列表"""
        alerts = self.alerts

        if alert_type:
            alerts = [a for a in alerts if a.alert_type == alert_type]

        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]

        return alerts[-limit:]

    def get_unacknowledged_alerts(self) -> List[Alert]:
        """获取未确认的告警"""
        return self.get_alerts(acknowledged=False)

    def acknowledge_alert(self, alert: Alert):
        """确认告警"""
        alert.acknowledge()
        logger.info(f"告警已确认: {alert.title}")

    def acknowledge_all(self):
        """确认所有告警"""
        for alert in self.alerts:
            alert.acknowledge()
        logger.info("所有告警已确认")

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        total = len(self.alerts)
        unacknowledged = len(self.get_unacknowledged_alerts())

        by_type = {
            alert_type: len([a for a in self.alerts if a.alert_type == alert_type])
            for alert_type in AlertType
        }

        return {
            "total_alerts": total,
            "unacknowledged": unacknowledged,
            "by_type": by_type
        }

    def clear(self):
        """清空告警"""
        self.alerts.clear()
        logger.info("告警已清空")

    def __repr__(self):
        stats = self.get_statistics()
        return (
            f"AlertManager(总数={stats['total_alerts']}, "
            f"未确认={stats['unacknowledged']})"
        )
