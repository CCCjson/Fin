"""
cninfo_job / research_report_job 状态自愈回归测试。

背景：Jason 报告过「进程停了，但 app 上还显示『停止中』」——根因是 _run() 只有
try/except Exception 没有 finally，且 snapshot()/stop() 不检查线程是否还活着。
三处缺陷叠加会让面板永久卡在 "stopping"，只能重启后端解除。

这里不跑真实网络/DB 的摄入逻辑，只验证状态机本身：
- snapshot() 能自愈「线程已死但 status 还没翻」
- stop() 对死线程复位而不是空手而归
- _run() 的 finally 兜住「非 Exception 提前跳出 try」的场景（模拟 BaseException）
- 正常完成 / stop_flag 路径不受影响（done vs stopped 的区分还在）

两个 job 结构逐字相同，参数化跑同一套测试。
"""

import pytest

pytestmark = pytest.mark.integration
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge_engine.ingest import cninfo_job as cninfo_mod  # noqa: E402
from knowledge_engine.ingest import research_report_job as rr_mod  # noqa: E402

JOB_MODULES = [
    pytest.param(cninfo_mod, cninfo_mod.CninfoIngestJob, id="cninfo"),
    pytest.param(rr_mod, rr_mod.ResearchReportIngestJob, id="research_report"),
]


def _empty_session(monkeypatch, mod):
    """把 get_session().query(...).all() 打桩成空列表，避免真实 DB 依赖。"""
    class _FakeQuery:
        def filter(self, *a, **kw):
            return self

        def order_by(self, *a, **kw):
            return self

        def all(self):
            return []

    class _FakeSession:
        def query(self, *a, **kw):
            return _FakeQuery()

        def close(self):
            pass

    monkeypatch.setattr(mod, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(mod, "_load_progress", lambda: {"done": [], "failed": []})
    monkeypatch.setattr(mod, "_save_progress", lambda progress: None)


@pytest.mark.parametrize("mod,cls", JOB_MODULES)
class TestIngestJobSelfHeal:

    def test_snapshot_self_heals_dead_thread_stopping(self, mod, cls):
        job = cls()
        # 模拟：stop() 已经把 status 设成 stopping，但线程随后死了
        # （原生崩溃/被杀——finally 根本没机会跑），从没被任何人发现。
        job.status = "stopping"
        job._thread = threading.Thread(target=lambda: None)
        job._thread.start()
        job._thread.join()  # 确保线程已经结束、is_alive()==False

        snap = job.snapshot()
        assert snap["status"] == "stopped"
        assert job.status == "stopped"
        assert job.current_symbol is None

    def test_snapshot_self_heals_dead_thread_running(self, mod, cls):
        """running 也要能自愈——不只是 stopping 那一档。"""
        job = cls()
        job.status = "running"
        job._thread = threading.Thread(target=lambda: None)
        job._thread.start()
        job._thread.join()

        snap = job.snapshot()
        assert snap["status"] == "stopped"

    def test_snapshot_no_thread_object_at_all(self, mod, cls):
        """_thread is None 但 status 残留 running（理论上不该发生，但要防御）。"""
        job = cls()
        job.status = "running"
        job._thread = None
        snap = job.snapshot()
        assert snap["status"] == "stopped"

    def test_snapshot_does_not_touch_healthy_states(self, mod, cls):
        """idle/done/stopped 已经是终态，不该被 snapshot 动。"""
        job = cls()
        for s in ("idle", "done", "stopped"):
            job.status = s
            job._thread = None
            job.snapshot()
            assert job.status == s

    def test_snapshot_leaves_alive_thread_alone(self, mod, cls):
        """线程还真的活着时，running/stopping 不该被误伤成 stopped。"""
        gate = threading.Event()
        job = cls()
        job.status = "running"
        job._thread = threading.Thread(target=gate.wait)
        job._thread.start()
        try:
            job.snapshot()
            assert job.status == "running"
        finally:
            gate.set()
            job._thread.join()

    def test_stop_on_dead_thread_resets_status(self, mod, cls):
        """点『停止』时线程其实已经死了——之前会空手而归，status 卡死不变。"""
        job = cls()
        job.status = "stopping"  # 假设上一次 stop() 已经设过
        job._thread = threading.Thread(target=lambda: None)
        job._thread.start()
        job._thread.join()

        result = job.stop()
        assert result["ok"] is False  # 「当前没有在跑的任务」——语义没变
        assert result["status"] == "stopped"  # 但状态被复位了，UI 能恢复
        assert job.status == "stopped"

    def test_stop_on_never_started_job_is_idle_noop(self, mod, cls):
        """从没 start 过的全新 job，stop() 不该把 idle 错误地翻成 stopped。"""
        job = cls()
        result = job.stop()
        assert result["ok"] is False
        assert result["status"] == "idle"
        assert job.status == "idle"

    def test_run_finally_survives_base_exception(self, mod, cls, monkeypatch):
        """_run() 内部抛 BaseException（非 Exception 子类，如 SystemExit）时，
        except Exception 不会捕获它，但 finally 必须仍然执行、把 status 兜回 stopped。
        这正是「进程线程非正常终止但 Python 解释器还在跑」这一档的回归锁。
        """
        job = cls()
        job.status = "running"
        job.config = {}

        def _boom():
            raise SystemExit("simulated abrupt termination")

        monkeypatch.setattr(mod, "get_session", _boom)

        with pytest.raises(SystemExit):
            job._run()

        assert job.status == "stopped"
        assert job.current_symbol is None

    def test_run_normal_completion_sets_done(self, mod, cls, monkeypatch):
        """空 todo 列表、没有 stop 信号 → 正常跑完 → done（不是 stopped）。"""
        _empty_session(monkeypatch, mod)
        job = cls()
        job.config = {
            "categories": ["年报"], "per_category": 1, "max_pages": 10,
            "start_date": "20240101", "end_date": "20261231",
            "sleep_between": 0, "max_retry": 1,
            "limit_per_symbol": 10, "full_text": False,
        }
        job._run()
        assert job.status == "done"

    def test_run_stop_flag_sets_stopped_not_done(self, mod, cls, monkeypatch):
        """提前设了 stop_flag → 即便正常跑完循环，也该是 stopped 而不是 done。"""
        _empty_session(monkeypatch, mod)
        job = cls()
        job.config = {
            "categories": ["年报"], "per_category": 1, "max_pages": 10,
            "start_date": "20240101", "end_date": "20261231",
            "sleep_between": 0, "max_retry": 1,
            "limit_per_symbol": 10, "full_text": False,
        }
        job._stop_flag.set()
        job._run()
        assert job.status == "stopped"

    def test_run_exception_path_still_sets_stopped(self, mod, cls, monkeypatch):
        """普通 Exception 崩溃路径（原有行为）不能被这次改动破坏。"""
        job = cls()
        job.status = "running"
        job.config = {}
        monkeypatch.setattr(mod, "get_session",
                            lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        job._run()  # 不应该向外抛
        assert job.status == "stopped"


@pytest.mark.parametrize("mod,cls", JOB_MODULES)
def test_start_start_stop_full_cycle_status_progression(mod, cls, monkeypatch):
    """端到端最小闭环：start → 轮询到 done → stop 在已完成后是 noop 且不报错。"""
    _empty_session(monkeypatch, mod)
    job = cls()
    resp = job.start(**({
        "categories": ["年报"], "per_category": 1, "max_pages": 5,
        "start_date": "20240101", "end_date": "20261231",
        "sleep_between": 0,
    } if cls is cninfo_mod.CninfoIngestJob else {
        "limit_per_symbol": 5, "full_text": False,
        "start_date": "20220101", "end_date": "20261231",
        "sleep_between": 0,
    }))
    assert resp["ok"] is True
    assert resp["status"] == "running"

    deadline = time.time() + 5
    while job.status == "running" and time.time() < deadline:
        time.sleep(0.02)

    assert job.status == "done"
    stop_resp = job.stop()
    assert stop_resp["ok"] is False
    assert stop_resp["status"] == "done"  # 已完成的任务再点停止：status 不被误改成 stopped


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
