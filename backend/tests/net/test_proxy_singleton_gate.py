"""防复发门禁：买 IP 的权力必须收口在 `net/` 里。

## 为什么需要这条门禁

`ProxyManager.get_proxy()` 三年前就写好了「当前 IP 没过期就复用」的逻辑——
**但全仓没有一个人调它**。真正的原因不是谁写错了一行，而是「谁能买 IP」这件事
从来没人管：七个模块各自 `ProxyManager()`，每个实例一份彼此看不见的
`current_proxy` 缓存，于是每条出网链路都在各买各的 IP。

光把调用点改对挡不住下一次。这条门禁挡的是**结构**：

1. `net/` 之外不许 `ProxyManager()` —— 一律走 `get_proxy_manager()` 单例
2. `net/` 之外不许调 `fetch_one_proxy()` —— 它无条件扣额度；
   想复用调 `get_proxy()`，想换 IP 调 `switch_proxy()`
3. `ProxyPool` 不许被当局部变量用完就扔（槽里躺着还没过期的 IP）

违反任何一条，代价是每次请求烧一个 IP。实测账单：8 天烧掉 8394 个。
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.baseline

BACKEND = pathlib.Path(__file__).resolve().parent.parent.parent

# 只有这些文件有权直接构造 ProxyManager / 调提取 API
_MAY_CONSTRUCT = {"net/proxy_manager.py", "net/proxy_pool.py"}
_MAY_FETCH = {"net/proxy_manager.py", "net/proxy_pool.py"}

_SKIP_DIRS = {"tests", "node_modules", "venv", ".venv", "__pycache__", "backtest_cpp"}


def _production_sources():
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if set(rel.parts) & _SKIP_DIRS:
            continue
        yield rel, path.read_text(encoding="utf-8")


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _callee_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


# ── 1. 不许自建 ProxyManager ────────────────────────────────────────────────

def _sanctioned_fallbacks(tree: ast.AST) -> set[int]:
    """`get_proxy_manager() or ProxyManager()` —— 唯一被认可的兜底惯用法。

    单例在未配快代理时返回 None，而离线批处理任务（daily_updater 等）需要一个
    对象来走 direct_mode。这个写法保证「配了就用单例、没配才自建空壳」。
    """
    ok: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        names = [_callee_name(v) for v in node.values if isinstance(v, ast.Call)]
        if "get_proxy_manager" not in names:
            continue
        for v in node.values:
            if isinstance(v, ast.Call) and _callee_name(v) == "ProxyManager":
                ok.add(v.lineno)
    return ok


def test_nobody_constructs_proxy_manager_outside_net():
    offenders = []
    for rel, src in _production_sources():
        if str(rel) in _MAY_CONSTRUCT:
            continue
        tree = ast.parse(src)
        sanctioned = _sanctioned_fallbacks(tree)
        for call in _calls(tree):
            if _callee_name(call) == "ProxyManager" and call.lineno not in sanctioned:
                offenders.append(f"{rel}:{call.lineno}")
    assert not offenders, (
        "这些地方自建了 ProxyManager，各持一份看不见的 IP 缓存 → 各买各的 IP。\n"
        "改用 `from net import get_proxy_manager`（离线任务可写 "
        "`get_proxy_manager() or ProxyManager()`）：\n  " + "\n  ".join(offenders)
    )


# ── 2. 不许直调提取 API ─────────────────────────────────────────────────────

def test_nobody_calls_fetch_one_proxy_outside_net():
    offenders = []
    for rel, src in _production_sources():
        if str(rel) in _MAY_FETCH:
            continue
        for call in _calls(ast.parse(src)):
            if _callee_name(call) == "fetch_one_proxy":
                offenders.append(f"{rel}:{call.lineno}")
    assert not offenders, (
        "fetch_one_proxy() 无条件打提取 API，每调一次买一个 IP。\n"
        "复用当前 IP 用 get_proxy()；请求失败后换 IP 用 switch_proxy()：\n  "
        + "\n  ".join(offenders)
    )


# ── 3. ProxyPool 不许当局部变量 ─────────────────────────────────────────────

def test_proxy_pool_is_never_a_throwaway_local():
    """池子一旦被 GC，槽里还没过期的 IP 就白买了。

    合法用法：模块级常驻（`_QUOTE_POOL`），或长任务里活满整个 job（daily_updater、
    financial、a_share_job —— 一个 job 一个池，跑十几分钟，不是每请求一个）。

    非法用法：短命函数里 `pool = ProxyPool(...)` 然后 return。这里只能靠白名单
    人工把关，所以新增一处就必须来这里登记，顺便被迫想一遍它活多久。
    """
    allowed = {
        "acquisition/markets/realtime.py",     # 模块级 _QUOTE_POOL，常驻
        "data_engine/daily_updater.py",        # 一个 job 一个池（~12min）
        "acquisition/markets/financial.py",    # 同上
        "data_engine/deep_history/a_share_job.py",
    }
    found = set()
    for rel, src in _production_sources():
        if str(rel) in {"net/proxy_pool.py"}:
            continue
        for call in _calls(ast.parse(src)):
            if _callee_name(call) == "ProxyPool":
                found.add(str(rel))
    unregistered = found - allowed
    assert not unregistered, (
        "新增了 ProxyPool 的构造点。确认它不是「用完就扔」的局部变量后，"
        f"登记到本测试的 allowed 里：{sorted(unregistered)}"
    )


# ── 4. 单例本身的契约 ───────────────────────────────────────────────────────

def test_get_proxy_manager_is_exported_from_net():
    import net

    assert hasattr(net, "get_proxy_manager")
    assert "get_proxy_manager" in net.__all__


def test_singleton_returns_the_same_object(monkeypatch):
    from net import proxy_manager as pmod

    monkeypatch.setenv("kuaidaili_api", "https://fake")
    pmod.reset_proxy_manager()
    assert pmod.get_proxy_manager() is pmod.get_proxy_manager()


def test_get_proxy_reuses_but_switch_proxy_buys(monkeypatch):
    """两个方法的语义分工，别再搞混。"""
    from datetime import datetime, timedelta

    from net.proxy_manager import ProxyInfo, ProxyManager

    monkeypatch.setenv("kuaidaili_api", "https://fake")
    pm = ProxyManager()
    calls = {"n": 0}

    def _fake():
        calls["n"] += 1
        pm.current_proxy = ProxyInfo(
            ip="1.2.3.4", port=8080,
            expire_at=(datetime.now() + timedelta(seconds=180)).isoformat())
        return pm.current_proxy

    monkeypatch.setattr(pm, "_fetch_one_proxy_locked", _fake)

    for _ in range(3):
        pm.get_proxy()
    assert calls["n"] == 1, "get_proxy 在有效期内必须复用"

    pm.switch_proxy()
    assert calls["n"] == 2, "switch_proxy 必须真的换一个"
