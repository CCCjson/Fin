"""
LivenessTracker 计时语义回归测试。

背景：2026-07-07 体检发现 daily_updater 慢路径的看门狗以「多久没有终态结果」
判定卡死，会被「失败重入队重试」这种活着但暂时没结果的情况误杀，且没有
时间驱动的心跳。`LivenessTracker` 把「活动」（touch）和「对外输出」
（mark_yield）分开计时，这里只验证纯计时逻辑本身，不涉及任何 IO。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_engine import liveness as liveness_mod  # noqa: E402
from data_engine.liveness import LivenessTracker  # noqa: E402


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_heartbeat_fires_after_interval_and_resets_on_mark_yield(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(liveness_mod.time, "time", clock.time)

    tracker = LivenessTracker(stall_timeout=180.0, heartbeat_interval=2.0)
    assert not tracker.should_heartbeat()

    clock.advance(1.9)
    assert not tracker.should_heartbeat()

    clock.advance(0.2)
    assert tracker.should_heartbeat()

    tracker.mark_yield()
    assert not tracker.should_heartbeat()


def test_touch_prevents_stall_but_does_not_affect_heartbeat(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(liveness_mod.time, "time", clock.time)

    tracker = LivenessTracker(stall_timeout=10.0, heartbeat_interval=2.0)

    clock.advance(9.9)
    assert not tracker.is_stalled()

    # touch（比如失败重入队的活动信号）重置活动计时，不应被判定为卡死
    tracker.touch()
    clock.advance(9.9)
    assert not tracker.is_stalled()

    clock.advance(0.2)
    assert tracker.is_stalled()
    assert tracker.seconds_since_activity() >= 10.0


def test_is_stalled_independent_of_last_yield(monkeypatch):
    """即使一直没有对外输出（mark_yield 从未调用），只要 touch 持续，就不算卡死。"""
    clock = _FakeClock()
    monkeypatch.setattr(liveness_mod.time, "time", clock.time)

    tracker = LivenessTracker(stall_timeout=5.0, heartbeat_interval=1.0)
    for _ in range(20):
        clock.advance(4.0)
        tracker.touch()
        assert not tracker.is_stalled()

    clock.advance(5.1)
    assert tracker.is_stalled()
