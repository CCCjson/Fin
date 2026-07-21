"""
进程内业务事件总线（单例）—— 让 schedulers/引擎产生的领域事件汇聚到一处，
供 MoneyBill 按需读取（get_system_pulse，event_limit 可调）、供前端实时展示活动流。

设计原则（对齐「不做主动 daemon」）：
- 只做可观测 + 状态汇聚，不在这里触发任何自主处置。
- 生产者调用零负担：publish_event() 吞掉一切异常，绝不影响主流程。
- 订阅者异常隔离：一个坏订阅者不会拖垮生产者或其它订阅者。
- 纯内存环形缓冲（deque maxlen），持久历史另有 DataUpdateLog/AutomationLog 表，不重复造。

顶层模块（与 decision_log.py 同层），底层中立，引擎/调度器可直接 import，不依赖 agents/automation。
"""
import collections
import uuid
from datetime import datetime
from typing import Callable, Optional

# ── 事件类型常量 ──
PIPELINE_DONE = "pipeline.done"
SIGNAL_GENERATED = "signal.generated"
ORDER_FILLED = "order.filled"
PORTFOLIO_CHANGED = "portfolio.changed"
NEWS_HIGH_IMPACT = "news.high_impact"
DATA_STALE = "data.stale"
SCAN_COMPLETED = "scan.completed"
RISK_ALERT = "risk.alert"
EARN_SWEEP = "crypto.earn_sweep"


class EventBus:
    def __init__(self, maxlen: int = 500):
        self._ring: collections.deque = collections.deque(maxlen=maxlen)
        self._subs: list[Callable[[dict], None]] = []

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        if fn not in self._subs:
            self._subs.append(fn)

    def unsubscribe(self, fn: Callable[[dict], None]) -> None:
        if fn in self._subs:
            self._subs.remove(fn)

    def publish(self, event_type: str, *, title: str, source: str = "system",
                severity: str = "info", symbol: Optional[str] = None, **data) -> dict:
        ev = {
            "id": uuid.uuid4().hex[:12],
            "ts": datetime.now().isoformat(),
            "type": event_type,
            "source": source,
            "severity": severity,   # info | warn | alert
            "title": title,
            "symbol": symbol,
            "data": data,
        }
        self._ring.append(ev)
        for fn in list(self._subs):
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 — 订阅者异常绝不外溢
                pass
        return ev

    def recent(self, limit: int = 20, types=None) -> list[dict]:
        """返回最近事件，最新在前。"""
        items = list(self._ring)
        if types:
            tset = set(types)
            items = [e for e in items if e["type"] in tset]
        return items[-limit:][::-1] if limit else items[::-1]


# 模块级单例
BUS = EventBus()


def publish_event(event_type: str, *, title: str, source: str = "system",
                  severity: str = "info", symbol: Optional[str] = None, **data) -> None:
    """便捷发布：吞掉一切异常，生产者调用点零负担。"""
    try:
        BUS.publish(event_type, title=title, source=source,
                    severity=severity, symbol=symbol, **data)
    except Exception:  # noqa: BLE001
        pass
