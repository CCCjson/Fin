"""派发到独立线程执行并阻塞取结果 —— 卡死的 worker 绝不拖累任何人。

⛔ 为什么**不用** `concurrent.futures.ThreadPoolExecutor`（2026-07-24 事故后拍板，
见 docs/GOTCHAS.md「proxy_route 自锁」）：

1. `with ThreadPoolExecutor(...) as ex:` 的 `__exit__` 走 `shutdown(wait=True)`
   去 join worker。worker 一旦卡死，join 就无限阻塞，把 `.result(timeout=)` 的
   超时保护整个吃掉——TimeoutError 连抛出来的机会都没有。
2. 就算改成手动 `shutdown(wait=False)` 躲开第 1 条，`concurrent.futures` 仍然
   注册了 atexit 钩子 `_python_exit`，**解释器退出时还是会 join 所有存活
   worker**：一个卡死的浏览器任务能让整个后端进程关不掉，`restart.sh` 卡在停机那步。
   （实测：门禁测试 38s 就跑完，进程却再也退不出来。）

daemon 线程两条都没有：超时就撒手，进程退出直接把它抛下。代价是超时后那个线程
仍在后台占着（没法从外面打断 Python 线程），但它至少不再挡着别人。
"""
import threading
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from loguru import logger


def run_in_daemon_thread(
    fn: Callable[..., Any],
    *args: Any,
    timeout_s: float,
    label: str | None = None,
    **kwargs: Any,
) -> Any:
    """在 daemon 线程里执行 fn 并阻塞取结果，超时抛 `TimeoutError`。

    Args:
        fn: 要执行的可调用对象。
        *args: 透传给 fn 的位置参数。
        timeout_s: 硬超时（秒）。超时后**不再等** worker，直接抛。
        label: 线程名后缀，便于 `sample` / py-spy 抓栈时认人。默认取 fn 名字。
        **kwargs: 透传给 fn 的关键字参数。

    Returns:
        fn 的返回值。

    Raises:
        concurrent.futures.TimeoutError: 超过 timeout_s 仍未跑完。
        Exception: fn 内部抛出的异常原样透传。
    """
    name = label or getattr(fn, "__name__", "task")
    box: dict = {}
    done = threading.Event()

    def _run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 原样带回主线程再抛，不在这吞
            box["error"] = e
        finally:
            done.set()

    threading.Thread(
        target=_run, name=f"offthread-{name}", daemon=True).start()

    if not done.wait(timeout=timeout_s):
        logger.error(
            f"[offthread] {name} 超时（>{timeout_s}s）已放弃等待"
            "（该线程可能仍卡在里面，但不再挡着调用方）")
        raise FutureTimeoutError(f"{name} 超时（>{timeout_s}s）")

    if "error" in box:
        raise box["error"]
    return box["value"]
