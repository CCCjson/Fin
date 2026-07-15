"""
常驻单例后台 job 基类 —— 收编原先散在 4 处（deep_history a_share/overseas、
knowledge_engine cninfo/research_report）逐字复制的「单例线程 + 进程内状态机 +
start/stop/snapshot + self-heal + finally 翻正」骨架。

契约（对前端/路由冻结，子类不得改形状）：
- start(...) / stop() / snapshot() 三个方法各返回一个 dict。
- snapshot() 的**公共交集字段**由基类固化：status / total_all / done_total /
  processed_this_run / rate_per_min / eta_seconds / elapsed_seconds / recent / config。
  各 job 独有的计数字段（current_symbol、success、market、excluded ...）走
  `_extra_snapshot_fields()` 钩子，逐字对齐各自原有输出，基类不得凭空增删。

self-heal（「停止中」永久卡死的通用防线，见 ingest-job-status-selfheal 经验）三处：
1. snapshot()：status 还在 running/stopping 但线程已不 alive → 就地复位 stopped。
   这是唯一能兜住「原生崩溃/线程被外部杀掉、Python 的 try/except/finally 根本没执行」
   那一档的防线。
2. _run() 的 finally：正常结束/Exception/BaseException 提前跳出 try 后，只要 status
   还停在 running/stopping 就强制翻正。
3. stop()：对已死线程也复位 status（而不是空手而归）。

抽基类顺带把 a_share/overseas 两份「退化版」（原来只有 _run 顶层 except、缺 snapshot
层与 stop 层自愈）拉齐到 cninfo/rr 的完整版。

status 收口（`stopped if stop else done`）、断点续传进度文件读写、业务循环体本身
仍留在各子类 `_execute()` 里——各 job 差异太大（ProxyPool 多 worker / yfinance 分批 /
单线程退避重试），且收口条件各有特例（a_share 的 abort_reason、overseas 的连通性预检
置 stopped）。基类只保证「无论子类怎么退出，status 都不会卡死」。
"""
import threading
import time
from abc import ABC, abstractmethod

from loguru import logger


class BaseSingletonJob(ABC):
    """常驻单例后台任务基类。子类实现 `_execute` 业务体 + 三个状态钩子。"""

    # ---- 可被子类覆盖的文案（默认值适用于单只逐一处理的 job）----
    JOB_NAME: str = "后台任务"
    ALREADY_RUNNING_MSG: str = "已经在跑了"
    STOPPING_MSG: str = "已发送停止信号，会在处理完当前这只后停下"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._state_lock = threading.Lock()
        self._reset_state()

    # ---------- 状态字段 ----------

    def _reset_state(self) -> None:
        """复位公共字段 + 委托子类复位业务字段。`config` 由 `_launch` 在此之后写入。"""
        self.status = "idle"  # idle / running / stopping / stopped / done
        self.total_all = 0
        self.done_total = 0
        self.processed_this_run = 0
        self.recent: list[dict] = []
        self.started_at: float | None = None
        self.config: dict = {}
        self._reset_extra_state()

    # ---------- 对外：起停 + 查状态 ----------

    def _launch(self, config: dict) -> dict:
        """`start` 骨架：子类各自的类型化 `start(**kwargs)` 组好 config dict 后转调此处。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "message": self.ALREADY_RUNNING_MSG, **self.snapshot()}

            self._stop_flag.clear()
            self._reset_state()
            self.config = config
            self.status = "running"
            self.started_at = time.time()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return {"ok": True, "message": "已启动", **self.snapshot()}

    def stop(self) -> dict:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                # 线程已经不在了（自然跑完/崩溃/被杀）：若 status 还残留 running/stopping
                # （没赶上 finally 或 snapshot 的自愈时机），这里顺手复位，避免用户点了
                # 「停止」却什么都没变、面板继续卡死。
                with self._state_lock:
                    if self.status in ("running", "stopping"):
                        self.status = "stopped"
                        self._clear_current()
                return {"ok": False, "message": "当前没有在跑的任务", **self.snapshot()}
            self._stop_flag.set()
            with self._state_lock:
                self.status = "stopping"
            return {"ok": True, "message": self.STOPPING_MSG, **self.snapshot()}

    def snapshot(self) -> dict:
        with self._state_lock:
            # self-heal ①：status 还停在 running/stopping，但 worker 线程已经不在了——
            # 典型是原生崩溃（C 扩展段错误）/线程被外部杀掉，Python 层 try/except/finally
            # 根本不会执行，只能靠这里发现即纠正。没有这一步「停止中」会永久卡死且无法
            # 通过 UI 恢复（唯一出路是重启后端）。
            if self.status in ("running", "stopping") and not (
                    self._thread is not None and self._thread.is_alive()):
                self.status = "stopped"
                self._clear_current()
            elapsed = (time.time() - self.started_at) if self.started_at else 0.0
            rate_per_min = (self.processed_this_run / elapsed * 60) if elapsed > 0 else 0.0
            remaining = self._remaining()
            eta_s = (remaining / (rate_per_min / 60)) if rate_per_min > 0 else None
            snap = {
                "status": self.status,
                "total_all": self.total_all,
                "done_total": self.done_total,
                "processed_this_run": self.processed_this_run,
                "rate_per_min": round(rate_per_min, 1),
                "eta_seconds": eta_s,
                "elapsed_seconds": elapsed,
                "recent": list(self.recent),
                "config": dict(self.config),
            }
            snap.update(self._extra_snapshot_fields())
            return snap

    # ---------- 后台线程实体 ----------

    def _run(self) -> None:
        """外壳：把业务体交给子类 `_execute`，包住异常日志 + finally 兜底翻正。

        status 的正常收口（done/stopped）与 success 日志留在子类 `_execute` 内（各 job
        收口条件与文案不同）。这里的 finally 只做兜底：无论正常结束、Exception、还是
        SystemExit/KeyboardInterrupt 等 BaseException 提前跳出 try，只要 status 还停在
        running/stopping 就强制翻正。注意 kill -9/原生段错误时本 finally 也不会执行，
        那一档由 snapshot()/stop() 的线程存活自愈兜底。
        """
        try:
            self._execute(dict(self.config))
        except Exception as e:  # noqa: BLE001 — 后台线程异常绝不能悄悄死掉不留痕迹
            logger.exception(f"{self.JOB_NAME} 线程异常退出: {e}")
        finally:
            with self._state_lock:
                if self.status in ("running", "stopping"):
                    self.status = "stopped"
                self._clear_current()

    # ---------- 子类必须实现 ----------

    @abstractmethod
    def _execute(self, cfg: dict) -> None:
        """真正的业务循环。自行负责 status 的正常收口（done/stopped）、进度文件读写、
        断点续传、recent 维护。异常无需自己吞（基类 `_run` 会记日志 + finally 兜底）。"""

    @abstractmethod
    def _reset_extra_state(self) -> None:
        """复位本 job 独有的状态字段（在 `_reset_state` 尾部被调用）。"""

    @abstractmethod
    def _extra_snapshot_fields(self) -> dict:
        """返回公共交集之外的快照字段，逐字对齐本 job 原有输出。

        **在 `_state_lock` 内被调用**，实现里直接读 self.xxx，不要再获取 `_state_lock`
        （threading.Lock 非重入，会自锁死）。"""

    # ---------- 子类可选覆盖（有默认实现）----------

    def _remaining(self) -> int:
        """eta 计算的剩余量分母。默认 total_all - done_total；有 failed_symbols 概念的
        job（cninfo/rr）覆写为再减去 failed_symbols。在 `_state_lock` 内被调用。"""
        return max(0, self.total_all - self.done_total)

    def _clear_current(self) -> None:
        """终态/自愈时清空「当前处理对象」。基类不知道子类用哪个字段（current_symbol
        还是 current_batch），子类按需覆写。在 `_state_lock` 内被调用。"""
        return
