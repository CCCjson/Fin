"""防复发门禁：浏览器/代理路由这条链，任何情况下都不许把调用线程无限吊住。

## 为什么需要这条门禁

2026-07-24 实锤事故：Jason 让 MoneyBill 查金价，一次 `scrape` 撞上国内域名，
整个对话会话卡死近 10 分钟，后端进程看着「健康」（/health 毫秒级 200）、
23 个线程里 22 个空闲，唯独 turn 线程永久停在一把 Python 锁上。

根因是两个单独看都没错的写法叠在一起：

1. `proxy_route._fetch()` 外面包了 `with _manager_lock`，里面调的
   `_get_manager()` **又拿同一把非重入 Lock** → 同线程双抢 = 永久自锁。
   而 `_manager` 只可能在那个被锁死的块里赋值，所以它永远是 None，
   每次调用都必然重演——不是偶发竞态，是 100% 必现。
2. 取代理那圈本来有 `.result(timeout=...)` 硬超时保护，但写成了
   `with ThreadPoolExecutor(...) as ex:` —— `__exit__` 会 shutdown(wait=True)
   去 join 那个已经死掉的 worker，**join 上无限阻塞，TimeoutError 连抛出来
   的机会都没有**。超时保护被 `with` 语句本身吃掉了。

死锁线程握着 session 的 run_lock 不放 → 用户再发消息一律 409 → 表现成整个
桌面 App 卡死。这条门禁挡的是结构，不只是那一行：

- 行为：`_domestic_proxy_info()` 必须在有限时间内返回或抛错，绝不无限等
- 结构：这条链上不许再出现 `with ThreadPoolExecutor(...)`
- 结构：这条链上的 `.result()` 必须带 timeout
"""
import ast
import pathlib
import threading

import pytest

pytestmark = pytest.mark.baseline

BACKEND = pathlib.Path(__file__).resolve().parent.parent.parent

# 这条链上「派发到线程再取结果」的全部文件（新增同类入口请一并纳管）
_GUARDED = (
    "acquisition/browser/proxy_route.py",
    "acquisition/browser/launch.py",
    "acquisition/browser/offthread.py",
)


def _parse(rel: str) -> ast.AST:
    return ast.parse((BACKEND / rel).read_text(encoding="utf-8"), filename=rel)


def _is_threadpool_ctor(node: ast.AST) -> bool:
    """判断表达式是不是在构造 ThreadPoolExecutor（认 `X()` 和 `a.b.X()` 两种写法）。"""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    return name == "ThreadPoolExecutor"


# ── 行为门禁：真去调一次，卡住就算挂 ────────────────────────────────────────

def test_domestic_proxy_info_never_hangs(monkeypatch):
    """`_domestic_proxy_info()` 必须有限时间内返回——这是事故的直接复现路径。

    自锁版本在这里会 100% 超时：worker 卡在 `_get_manager()` 抢锁，主线程卡在
    线程池 `__exit__` 的 join 上，两边都永远不回来。
    """
    from acquisition.browser import proxy_route
    from net import proxy_manager as pm

    class _FakeProxyInfo:
        url = "http://1.2.3.4:8888"

    class _FakeManager:
        def get_proxy(self):
            return _FakeProxyInfo()

    # 单例清空，强制走「首次初始化」那条正是当年死锁的路径
    monkeypatch.setattr(proxy_route, "_manager", None)
    monkeypatch.setattr(pm, "get_proxy_manager", lambda: _FakeManager())
    monkeypatch.setenv("KNOWLEDGE_SCRAPER_PROXY_ENABLED", "true")

    box: dict = {}
    done = threading.Event()

    def _run():
        try:
            box["pi"] = proxy_route._domestic_proxy_info()
        except Exception as e:  # noqa: BLE001
            box["err"] = e
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True).start()
    assert done.wait(timeout=30), (
        "_domestic_proxy_info() 30 秒没返回 = 死锁复发。"
        "多半是又有人在 _fetch 外面包了 with _manager_lock，"
        "或者把线程池改回了 with 写法（join 会吃掉超时）"
    )
    assert "err" not in box, f"不该抛异常，却抛了：{box.get('err')}"
    assert box["pi"].url == "http://1.2.3.4:8888"


def test_domestic_proxy_info_is_repeatable(monkeypatch):
    """连调两次必须都通——自锁版第一次就把锁永久带走，第二次起全员阻塞。"""
    from acquisition.browser import proxy_route
    from net import proxy_manager as pm

    class _FakeManager:
        def get_proxy(self):
            return object()

    monkeypatch.setattr(proxy_route, "_manager", None)
    monkeypatch.setattr(pm, "get_proxy_manager", lambda: _FakeManager())
    monkeypatch.setenv("KNOWLEDGE_SCRAPER_PROXY_ENABLED", "true")

    def _call(flag: threading.Event) -> None:
        try:
            proxy_route._domestic_proxy_info()
        finally:
            flag.set()

    for i in range(2):
        done = threading.Event()
        threading.Thread(target=_call, args=(done,), daemon=True).start()
        assert done.wait(timeout=30), f"第 {i + 1} 次调用卡死"


# ── 结构门禁：挡住写法本身 ──────────────────────────────────────────────────

@pytest.mark.parametrize("rel", _GUARDED)
def test_no_threadpool_executor_at_all(rel):
    """这条链上一律不许用 ThreadPoolExecutor —— 无论 with 还是手动 shutdown。

    两条都躲不掉的坑：
      1. `with ThreadPoolExecutor(...)` 的 __exit__ 走 shutdown(wait=True)，
         卡死的 worker 让 join 永不返回，超时保护形同虚设；
      2. 就算手动 shutdown(wait=False)，`concurrent.futures` 的 atexit 钩子
         `_python_exit` 退出时照样 join 所有存活 worker——一个卡死的浏览器任务
         能让整个后端进程关不掉，restart.sh 卡在停机那步。

    走 `offthread.run_in_daemon_thread`：daemon 线程两条都没有。
    """
    src = (BACKEND / rel).read_text(encoding="utf-8")
    code_lines = [
        (i, ln) for i, ln in enumerate(src.splitlines(), 1)
        if "ThreadPoolExecutor" in ln and not ln.lstrip().startswith("#")
    ]
    # 文档字符串里提这个名字是允许的（就是在讲为什么不能用），只挡真正的构造调用
    offenders = [
        node.lineno for node in ast.walk(ast.parse(src, filename=rel))
        if _is_threadpool_ctor(node)
    ]
    assert not offenders, (
        f"{rel} 第 {offenders} 行又构造 ThreadPoolExecutor 了"
        f"（原文：{[ln.strip() for i, ln in code_lines if i in offenders]}）。"
        "改用 acquisition.browser.offthread.run_in_daemon_thread。"
    )


@pytest.mark.parametrize("rel", _GUARDED)
def test_future_result_always_has_timeout(rel):
    """这条链上的 `.result()` 必须带超时——裸 result() 就是无限等。"""
    offenders = [
        node.lineno
        for node in ast.walk(_parse(rel))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "result"
        and not node.args
        and not any(kw.arg == "timeout" for kw in node.keywords)
    ]
    assert not offenders, (
        f"{rel} 第 {offenders} 行是裸 `.result()`，会无限等。"
        "这条链上游就是 MoneyBill 的 turn 线程，它一挂整个会话 409 到天荒地老。"
    )


def test_run_in_daemon_thread_times_out_instead_of_hanging():
    """worker 永远不返回时，调用方必须按时拿到 TimeoutError 而不是一起卡死。"""
    import concurrent.futures

    from acquisition.browser.offthread import run_in_daemon_thread

    never = threading.Event()   # 永不 set，模拟卡死的浏览器任务
    with pytest.raises(concurrent.futures.TimeoutError):
        run_in_daemon_thread(never.wait, timeout_s=1.0, label="wedged")


def test_wedged_worker_does_not_block_process_exit():
    """卡死的 worker 不许挡住解释器退出 —— 这正是 ThreadPoolExecutor 的致命伤。

    用 ThreadPoolExecutor 写同样的逻辑，子进程会永远退不出来（atexit 里 join
    那个卡死的 worker）。daemon 线程版必须干脆利落地退出。
    """
    import subprocess
    import sys

    code = "\n".join([
        f"import sys, threading; sys.path.insert(0, {str(BACKEND)!r})",
        "import concurrent.futures",
        "from acquisition.browser.offthread import run_in_daemon_thread",
        "never = threading.Event()",
        "try:",
        "    run_in_daemon_thread(never.wait, timeout_s=0.5, label='wedged')",
        "except concurrent.futures.TimeoutError:",
        "    print('timed-out-ok')",
    ])

    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert "timed-out-ok" in proc.stdout, f"子进程输出异常：{proc.stdout}{proc.stderr}"
    # subprocess.run 的 timeout 到点会抛 TimeoutExpired，能走到这说明进程自己退干净了


def test_get_manager_holds_the_lock_itself():
    """`_get_manager` 必须自己拿锁——它是唯一有权碰 `_manager_lock` 的初始化路径。

    真正要挡的是「调用方在外面再包一层」，但那属于调用侧；这条只钉死内部契约，
    免得有人反过来把 `_get_manager` 里的锁删掉、再把外层的锁加回去（等价 bug）。
    """
    tree = _parse("acquisition/browser/proxy_route.py")
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_get_manager"
    )
    locked = [
        item.context_expr.id
        for n in ast.walk(fn) if isinstance(n, ast.With)
        for item in n.items
        if isinstance(item.context_expr, ast.Name)
    ]
    assert "_manager_lock" in locked, "_get_manager 内部必须 `with _manager_lock`"


def test_fetch_does_not_double_lock():
    """`_domestic_proxy_info._fetch` 里不许再 `with _manager_lock`（自锁的直接元凶）。"""
    tree = _parse("acquisition/browser/proxy_route.py")
    outer = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_domestic_proxy_info"
    )
    fetch = next(
        n for n in ast.walk(outer)
        if isinstance(n, ast.FunctionDef) and n.name == "_fetch"
    )
    bad = [
        n.lineno
        for n in ast.walk(fetch) if isinstance(n, ast.With)
        for item in n.items
        if isinstance(item.context_expr, ast.Name)
        and item.context_expr.id == "_manager_lock"
    ]
    assert not bad, (
        f"_fetch 第 {bad} 行又在外面包 `with _manager_lock` 了。"
        "它调的 _get_manager() 内部拿的是同一把非重入 Lock，双抢必然永久自锁。"
    )
