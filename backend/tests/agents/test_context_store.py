"""
SessionStore 驱逐竞态 + 磁盘扫描节流回归测试（code-review 发现的两个 bug）。

1. _cleanup_locked() 曾经只看 updated_at 就驱逐——长耗时 turn 中途不 touch()，
   session 可能在仍被 run_lock 持有时被逐出内存，后续同 session_id 请求会
   从磁盘复活出一个全新、未锁定的对象，跟仍在跑的旧对象并发写导致互相覆盖。
2. 磁盘落盘文件 GC 曾经无节流地跟着每次 get_or_create() 跑，且占着全局锁，
   序列化了所有并发会话请求。

全部用 pytest 的 monkeypatch fixture 管理环境变量（用完自动还原成测试前的
原始状态），不用手写 os.environ[...] = ... / finally 还原——手写还原很容易
把值还原成写死的常量而不是"测试开始前的真实值"，污染同一进程里跑在后面的
其它测试（这个坑本身就是这份文件第一版踩过的）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unittest.mock import patch  # noqa: E402

from agents.context import AgentSession, SessionStore  # noqa: E402


def test_cleanup_skips_session_with_locked_run_lock():
    store = SessionStore(max_age=1)  # 1 秒就算"过期"，便于测试触发驱逐条件
    sess = AgentSession(session_id="mb_000000000001")
    sess.updated_at = time.time() - 10  # 早就超过 max_age
    sess.run_lock.acquire()  # 模拟"正在跑一个长耗时 turn"
    try:
        store._sessions[sess.session_id] = sess
        store._cleanup_locked()
        assert sess.session_id in store._sessions, "run_lock 被持有的 session 不应该被驱逐"
    finally:
        sess.run_lock.release()


def test_cleanup_evicts_stale_unlocked_session():
    store = SessionStore(max_age=1)
    sess = AgentSession(session_id="mb_000000000002")
    sess.updated_at = time.time() - 10
    store._sessions[sess.session_id] = sess
    store._cleanup_locked()
    assert sess.session_id not in store._sessions, "未被锁定的过期 session 应该正常驱逐"


def test_disk_cleanup_is_throttled(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_PERSIST", "on")
    store = SessionStore()
    with patch("agents.context._PERSIST_DIR") as mock_dir:
        mock_dir.glob.return_value = []
        store._cleanup_disk()
        store._cleanup_disk()
        store._cleanup_disk()
        assert mock_dir.glob.call_count == 1, "节流窗口内应该只真正扫描一次磁盘"


def test_disk_cleanup_runs_again_after_interval(monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_PERSIST", "on")
    store = SessionStore()
    with patch("agents.context._PERSIST_DIR") as mock_dir:
        mock_dir.glob.return_value = []
        store._cleanup_disk()
        store._last_disk_cleanup_at = time.time() - 9999  # 模拟节流窗口已过
        store._cleanup_disk()
        assert mock_dir.glob.call_count == 2


def test_get_or_create_does_not_hold_lock_during_disk_cleanup(monkeypatch):
    """磁盘清理必须发生在 self._lock 释放之后——用一个会在 glob() 里检查锁是否
    被占用的探针来验证，而不是猜测代码结构。"""
    monkeypatch.setenv("AGENT_SESSION_PERSIST", "on")
    store = SessionStore()
    probe = {"lock_held_during_glob": None}

    class _FakeDir:
        def glob(self, pattern):
            probe["lock_held_during_glob"] = store._lock.locked()
            return []

    with patch("agents.context._PERSIST_DIR", _FakeDir()):
        store.get_or_create(None)
    assert probe["lock_held_during_glob"] is False, (
        "磁盘扫描发生时不应该还占着 self._lock，否则会序列化所有并发会话请求")
