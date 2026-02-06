"""
交易日志记录器
"""
import json
from typing import Dict, List
from datetime import datetime
from pathlib import Path
from loguru import logger


class TradeLogger:
    """交易日志记录器 - 记录所有交易活动"""

    def __init__(self, log_dir: str = "./logs/trades"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.trade_log_file = self.log_dir / f"trades_{datetime.now().strftime('%Y%m%d')}.jsonl"
        self.event_log_file = self.log_dir / f"events_{datetime.now().strftime('%Y%m%d')}.jsonl"

        self.trades: List[Dict] = []
        self.events: List[Dict] = []

        logger.info(f"交易日志初始化: {self.log_dir}")

    def log_trade(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        commission: float,
        order_id: str,
        strategy: str = None,
        metadata: Dict = None
    ):
        """记录交易"""
        trade = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "price": price,
            "value": quantity * price,
            "commission": commission,
            "order_id": order_id,
            "strategy": strategy,
            "metadata": metadata or {}
        }

        self.trades.append(trade)
        self._write_log(self.trade_log_file, trade)

        logger.info(
            f"交易记录: {symbol} {action} {quantity}@{price:.2f} "
            f"(订单#{order_id})"
        )

    def log_event(
        self,
        event_type: str,
        message: str,
        level: str = "INFO",
        data: Dict = None
    ):
        """记录事件"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "level": level,
            "message": message,
            "data": data or {}
        }

        self.events.append(event)
        self._write_log(self.event_log_file, event)

        log_method = getattr(logger, level.lower(), logger.info)
        log_method(f"事件记录: [{event_type}] {message}")

    def log_position_change(
        self,
        symbol: str,
        old_quantity: int,
        new_quantity: int,
        avg_cost: float,
        current_price: float
    ):
        """记录持仓变化"""
        self.log_event(
            "POSITION_CHANGE",
            f"{symbol} 持仓变化: {old_quantity} -> {new_quantity}",
            "INFO",
            {
                "symbol": symbol,
                "old_quantity": old_quantity,
                "new_quantity": new_quantity,
                "avg_cost": avg_cost,
                "current_price": current_price
            }
        )

    def log_risk_alert(self, rule_name: str, message: str, severity: str):
        """记录风险告警"""
        self.log_event(
            "RISK_ALERT",
            f"{rule_name}: {message}",
            severity,
            {"rule_name": rule_name, "message": message}
        )

    def log_strategy_signal(
        self,
        strategy_name: str,
        symbol: str,
        signal_type: str,
        strength: float,
        reason: str
    ):
        """记录策略信号"""
        self.log_event(
            "STRATEGY_SIGNAL",
            f"{strategy_name} 生成信号: {symbol} {signal_type}",
            "INFO",
            {
                "strategy": strategy_name,
                "symbol": symbol,
                "signal_type": signal_type,
                "strength": strength,
                "reason": reason
            }
        )

    def _write_log(self, file_path: Path, data: Dict):
        """写入日志文件"""
        try:
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"写入日志失败: {e}")

    def get_trades(
        self,
        symbol: str = None,
        start_time: datetime = None,
        end_time: datetime = None
    ) -> List[Dict]:
        """查询交易记录"""
        trades = self.trades

        if symbol:
            trades = [t for t in trades if t["symbol"] == symbol]

        if start_time:
            trades = [t for t in trades if datetime.fromisoformat(t["timestamp"]) >= start_time]

        if end_time:
            trades = [t for t in trades if datetime.fromisoformat(t["timestamp"]) <= end_time]

        return trades

    def get_events(
        self,
        event_type: str = None,
        level: str = None,
        limit: int = 100
    ) -> List[Dict]:
        """查询事件记录"""
        events = self.events

        if event_type:
            events = [e for e in events if e["event_type"] == event_type]

        if level:
            events = [e for e in events if e["level"] == level]

        return events[-limit:]

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        total_trades = len(self.trades)
        buy_trades = len([t for t in self.trades if t["action"] == "BUY"])
        sell_trades = len([t for t in self.trades if t["action"] == "SELL"])
        total_commission = sum(t["commission"] for t in self.trades)
        total_value = sum(t["value"] for t in self.trades)

        return {
            "total_trades": total_trades,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "total_commission": total_commission,
            "total_value": total_value,
            "total_events": len(self.events)
        }

    def export_to_csv(self, output_file: str):
        """导出交易记录到CSV"""
        import pandas as pd

        if not self.trades:
            logger.warning("无交易记录")
            return

        df = pd.DataFrame(self.trades)
        df.to_csv(output_file, index=False)
        logger.info(f"交易记录已导出: {output_file}")

    def __repr__(self):
        stats = self.get_statistics()
        return (
            f"TradeLogger(交易={stats['total_trades']}, "
            f"事件={stats['total_events']})"
        )
