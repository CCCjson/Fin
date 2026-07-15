"""
BaseSingletonJob 基类回归测试 —— 覆盖全部 4 份继承它的常驻单例 job。

第13步大重构域3把 deep_history(a_share/overseas) 与 knowledge_engine(cninfo/
research_report) 四份逐字复制的生命周期骨架收编进 `data_engine.base_job.
BaseSingletonJob`。cninfo/rr 原本就有完整 self-heal（见 test_ingest_job.py），
a_share/overseas 是「退化版」（缺 snapshot 层 + stop 层自愈），抽基类顺带把它们
拉齐——这里就是那次拉齐 + 契约冻结的回归锁：

- snapshot()/stop() 的三处 self-heal 对四份统一生效（防「停止中」永久卡死）；
- 每份 job 的 snapshot() 键集与重构前逐字段一致（对前端契约冻结）；
- _run() 的 finally 兜住 BaseException（如 SystemExit）提前跳出 _execute 的场景；
- 正常跑完（空 universe）→ done，不受基类外壳影响。
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.integration

from data_engine.deep_history import a_share_job as ashare_mod  # noqa: E402
from data_engine.deep_history import overseas_job as overseas_mod  # noqa: E402
from knowledge_engine.ingest import cninfo_job as cninfo_mod  # noqa: E402
from knowledge_engine.ingest import research_report_job as rr_mod  # noqa: E402


class _FakeQuery:
    def filter(self, *a, **kw):
        return self

    def order_by(self, *a, **kw):
        return self

    def group_by(self, *a, **kw):
        return self

    def distinct(self, *a, **kw):
        return self

    def all(self):
        return []

    def scalar(self):
        return None


class _FakeSession:
    def query(self, *a, **kw):
        return _FakeQuery()

    def close(self):
        pass

    def rollback(self):
        pass


# 每份 job 的：模块 / 类 / start 关键字 / 重构前 snapshot 键集 / _load_progress 桩返回
CNINFO_KEYS = {
    "status", "total_all", "done_total", "failed_symbols", "processed_this_run",
    "ingested", "skipped", "failed_docs", "current_symbol",
    "rate_per_min", "eta_seconds", "elapsed_seconds", "recent", "config",
}
ASHARE_KEYS = {
    "status", "total_all", "done_total", "processed_this_run",
    "success", "failed", "new_records", "current_symbol",
    "rate_per_min", "eta_seconds", "elapsed_seconds", "recent", "config",
    "proxy_state", "abort_reason", "last_activity_ago_seconds",
}
OVERSEAS_KEYS = {
    "status", "market", "total_all", "done_total", "processed_this_run",
    "success", "no_data", "failed_batches", "new_records", "excluded", "current_batch",
    "rate_per_min", "eta_seconds", "elapsed_seconds", "recent", "config",
    "last_activity_ago_seconds",
}

JOB_SPECS = [
    pytest.param(
        cninfo_mod, cninfo_mod.CninfoIngestJob,
        {"categories": ["年报"], "per_category": 1, "max_pages": 5,
         "start_date": "20240101", "end_date": "20261231", "sleep_between": 0},
        CNINFO_KEYS, {"done": [], "failed": []}, id="cninfo",
    ),
    pytest.param(
        rr_mod, rr_mod.ResearchReportIngestJob,
        {"limit_per_symbol": 5, "full_text": False,
         "start_date": "20220101", "end_date": "20261231", "sleep_between": 0},
        CNINFO_KEYS, {"done": [], "failed": []}, id="research_report",
    ),
    pytest.param(
        ashare_mod, ashare_mod.AShareDeepHistoryJob,
        {"symbols": ["000001.SZ"], "limit": 1},
        ASHARE_KEYS, {"confirmed": {}}, id="a_share",
    ),
    pytest.param(
        overseas_mod, overseas_mod.OverseasDeepHistoryJob,
        {"market": "hk_stock", "symbols": ["00700.HK"], "limit": 1,
         "sleep_between_batches": 0},
        OVERSEAS_KEYS,
        {"hk_stock": {"confirmed_no_data": []}, "us_stock": {"confirmed_no_data": []}},
        id="overseas",
    ),
]


def _install_db_stubs(monkeypatch, mod, progress):
    monkeypatch.setattr(mod, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(mod, "_load_progress", lambda: dict(progress))
    monkeypatch.setattr(mod, "_save_progress", lambda p: None)


@pytest.mark.parametrize("mod,cls,start_kw,keys,progress", JOB_SPECS)
class TestBaseSingletonJobContract:

    def test_snapshot_key_set_frozen(self, mod, cls, start_kw, keys, progress):
        """snapshot() 键集必须与重构前逐字段一致（对前端契约冻结）。"""
        assert set(cls().snapshot().keys()) == keys

    def test_snapshot_self_heals_dead_thread_running(self, mod, cls, start_kw, keys, progress):
        """running 但线程已死 → snapshot 就地复位 stopped（a_share/overseas 原本缺这档）。"""
        job = cls()
        job.status = "running"
        job._thread = threading.Thread(target=lambda: None)
        job._thread.start()
        job._thread.join()
        assert job.snapshot()["status"] == "stopped"
        assert job.status == "stopped"

    def test_snapshot_self_heals_dead_thread_stopping(self, mod, cls, start_kw, keys, progress):
        job = cls()
        job.status = "stopping"
        job._thread = None
        assert job.snapshot()["status"] == "stopped"

    def test_snapshot_leaves_healthy_states_alone(self, mod, cls, start_kw, keys, progress):
        job = cls()
        for s in ("idle", "done", "stopped"):
            job.status = s
            job._thread = None
            job.snapshot()
            assert job.status == s

    def test_snapshot_leaves_alive_thread_alone(self, mod, cls, start_kw, keys, progress):
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

    def test_stop_on_dead_thread_resets_status(self, mod, cls, start_kw, keys, progress):
        """点『停止』时线程其实已死 → 复位 stopped（不空手而归、UI 能恢复）。"""
        job = cls()
        job.status = "stopping"
        job._thread = threading.Thread(target=lambda: None)
        job._thread.start()
        job._thread.join()
        result = job.stop()
        assert result["ok"] is False
        assert result["status"] == "stopped"
        assert job.status == "stopped"

    def test_stop_on_never_started_is_idle_noop(self, mod, cls, start_kw, keys, progress):
        job = cls()
        result = job.stop()
        assert result["ok"] is False
        assert result["status"] == "idle"

    def test_run_finally_survives_base_exception(self, mod, cls, start_kw, keys, progress, monkeypatch):
        """_execute 内抛 SystemExit（BaseException，except Exception 不捕获）时，
        基类 _run 的 finally 必须仍把 status 兜回 stopped。"""
        def _boom():
            raise SystemExit("simulated abrupt termination")

        monkeypatch.setattr(mod, "get_session", _boom)
        job = cls()
        job.status = "running"
        # overseas 的 _execute 先读 cfg['market']，其余读 get_session 前不依赖 config
        job.config = {"market": "hk_stock"}
        with pytest.raises(SystemExit):
            job._run()
        assert job.status == "stopped"

    def test_run_exception_path_sets_stopped(self, mod, cls, start_kw, keys, progress, monkeypatch):
        """普通 Exception 崩溃路径：_run 不外抛，status 兜到 stopped。"""
        monkeypatch.setattr(mod, "get_session",
                            lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        job = cls()
        job.status = "running"
        job.config = {"market": "hk_stock"}
        job._run()
        assert job.status == "stopped"

    def test_normal_completion_empty_universe_done(self, mod, cls, start_kw, keys, progress, monkeypatch):
        """空 universe（无待处理）→ 正常跑完 → done。"""
        _install_db_stubs(monkeypatch, mod, progress)
        job = cls()
        resp = job.start(**start_kw)
        assert resp["ok"] is True
        deadline = time.time() + 5
        while job.status == "running" and time.time() < deadline:
            time.sleep(0.02)
        assert job.status == "done"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
