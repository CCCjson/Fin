"""
跨 worker 活性跟踪器 — 供并发抓取任务（DailyUpdater/深历史/财务回补等）的
消费循环判定「是否该发心跳」「是否真的卡死」，与「多久没拿到终态结果」解耦。

背景：并发慢路径里失败会重入队重试，重试期间不产出终态结果，但 worker 明明
在动（换 IP、重试）。旧看门狗以「多久没有终态结果」判定 stall，会被这类
「活着但暂时没结果」的情况误杀；也没有时间驱动的心跳，导致代理故障时前端
长时间收不到任何进度事件。这里把「活动」（含失败/重试）和「对外输出」分开
计时，主循环按 `touch()` 判活、按 `should_heartbeat()` 决定要不要补发一帧。
"""
import time


class LivenessTracker:
    """纯计时器，不做任何 IO。"""

    def __init__(self, stall_timeout: float = 180.0, heartbeat_interval: float = 2.0) -> None:
        self.stall_timeout = stall_timeout
        self.heartbeat_interval = heartbeat_interval
        now = time.time()
        self._last_activity_at = now
        self._last_yield_at = now

    def touch(self) -> None:
        """记录一次 worker 活动（终态结果、失败重试等，凡是「还活着」的信号）。"""
        self._last_activity_at = time.time()

    def mark_yield(self) -> None:
        """记录一次对外输出（发了一帧 progress/心跳）。"""
        self._last_yield_at = time.time()

    def should_heartbeat(self) -> bool:
        """距上次对外输出是否已超过心跳间隔。"""
        return time.time() - self._last_yield_at >= self.heartbeat_interval

    def is_stalled(self) -> bool:
        """距上次活动是否已超过静默阈值（真正意义上的卡死）。"""
        return time.time() - self._last_activity_at >= self.stall_timeout

    def seconds_since_activity(self) -> float:
        return time.time() - self._last_activity_at
