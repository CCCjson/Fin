"""
内存泄漏追踪器（默认关闭，零成本）——用于抓「后端跑久了内存无界涨」的现行。

开启方式：环境变量 FIN_MEMTRACE=1 启动后端即可。
可选：
  FIN_MEMTRACE_INTERVAL  两次快照间隔秒数（默认 180）
  FIN_MEMTRACE_TOP       每次打印增长最快的前 N 个分配点（默认 15）
  FIN_MEMTRACE_LOG       日志路径（默认 /tmp/fin-memtrace.log）

原理：tracemalloc 定期快照，snapshot.compare_to 上一张，按「本区间字节增量」
降序列出 file:line + 累计大小，谁在无界涨一目了然。同时打印 tracemalloc
自身统计的 current/peak 以及进程 RSS 峰值，方便看整体趋势。

顶层模块（与 business_events.py 同层），无三方依赖，import 即可用。
"""
import os
import resource
import threading
import time
import tracemalloc
from datetime import datetime


def _enabled() -> bool:
    return os.getenv("FIN_MEMTRACE", "").lower() in ("1", "true", "yes", "on")


def _rss_mb() -> float:
    # macOS 上 ru_maxrss 单位是字节（Linux 是 KB）。这里按 mac 处理。
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def _monitor_loop(interval: int, top_n: int, log_path: str) -> None:
    prev = None
    round_no = 0
    while True:
        try:
            time.sleep(interval)
            round_no += 1
            snap = tracemalloc.take_snapshot()
            # 过滤掉 tracemalloc / 本模块自身的噪声
            snap = snap.filter_traces((
                tracemalloc.Filter(False, tracemalloc.__file__),
                tracemalloc.Filter(False, __file__),
            ))
            cur, peak = tracemalloc.get_traced_memory()
            lines = [
                "",
                "=" * 90,
                f"[memtrace #{round_no}] {datetime.now().isoformat(timespec='seconds')}  "
                f"traced_cur={cur/1e6:.1f}MB  traced_peak={peak/1e6:.1f}MB  rss_peak={_rss_mb():.1f}MB",
            ]
            if prev is not None:
                diff = snap.compare_to(prev, "lineno")
                diff.sort(key=lambda s: s.size_diff, reverse=True)
                lines.append(f"--- 本区间({interval}s)增长最快的 {top_n} 个分配点 ---")
                for stat in diff[:top_n]:
                    frame = stat.traceback[0]
                    lines.append(
                        f"  +{stat.size_diff/1024:>10.1f} KB  (累计 {stat.size/1024:>10.1f} KB, "
                        f"{stat.count_diff:+d} 块)  {frame.filename}:{frame.lineno}"
                    )
            else:
                lines.append("(首张快照，作为基线，下一轮开始出增量)")
            with open(log_path, "a") as f:
                f.write("\n".join(lines) + "\n")
            prev = snap
        except Exception as e:  # noqa: BLE001 — 监控线程绝不能拖垮主服务
            try:
                with open(log_path, "a") as f:
                    f.write(f"[memtrace] 监控异常（已忽略）: {e}\n")
            except Exception:
                pass


def maybe_start_memtrace() -> None:
    """若 FIN_MEMTRACE 开启，启动 tracemalloc + 后台快照线程。幂等、吞异常。"""
    if not _enabled():
        return
    try:
        interval = int(os.getenv("FIN_MEMTRACE_INTERVAL", "180"))
        top_n = int(os.getenv("FIN_MEMTRACE_TOP", "15"))
        log_path = os.getenv("FIN_MEMTRACE_LOG", "/tmp/fin-memtrace.log")
        frames = int(os.getenv("FIN_MEMTRACE_FRAMES", "12"))

        if not tracemalloc.is_tracing():
            tracemalloc.start(frames)

        with open(log_path, "a") as f:
            f.write(
                f"\n\n########## memtrace 启动 {datetime.now().isoformat(timespec='seconds')} "
                f"interval={interval}s top={top_n} frames={frames} ##########\n"
            )

        t = threading.Thread(
            target=_monitor_loop, args=(interval, top_n, log_path),
            name="memtrace", daemon=True,
        )
        t.start()
    except Exception:  # noqa: BLE001 — 追踪器不可用也绝不影响主服务
        pass
