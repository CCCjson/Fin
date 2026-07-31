"""🔴 **本机服务调用必须绕过代理**。

## 这条门禁在防什么

`.env` 里有 `HTTP_PROXY=http://127.0.0.1:7897`（Clash）而**没有 `NO_PROXY`**，
并且 `load_dotenv(override=True)` 在这个项目里有六七处。只要跑过任意一处，
`requests` / `httpx`（`trust_env` 默认 True）就会把
`http://localhost:8002`（C++ 回测）、`http://localhost:8001`（订单簿撮合）
也塞进代理。

Clash 没开的时候整条链路直接断，报的是
`ProxyError: Unable to connect to proxy` —— **看上去完全不像那个服务的问题**，
排查会绕很远。

⛔ 别把这些 `proxies=` / `trust_env=False` 当成「多余的样板」清理掉。
这正是最容易被下一个人顺手删掉、而删掉之后要过很久才会发现的那种代码
（实测：删掉之后全套 2000 条测试**全绿**，所以才需要这条门禁）。

📖 `docs/CODING_STANDARDS.md` §8 硬规矩 6 点名允许 localhost 内部服务裸调，
不走代理层 —— 与「国内抓取不许降级直连」那条铁律**不冲突**：
那条管的是**出网**抓数据，本机进程间调用压根没出网。
"""
import ast
import inspect

import pytest


def test_backtest_client_disables_proxy_for_localhost():
    """C++ 回测（:8002）的唯一出口必须显式关掉代理。"""
    from services import backtest_cpp_client as c

    # ⚠️ 断言 `_no_proxy()` 而不是那个常量：requests 会对传进去的 dict 做原地
    #    setdefault，共用常量会被环境里其它 scheme 的代理污染（踩过）。
    assert c._no_proxy() == {"http": None, "https": None}
    assert c._no_proxy() is not c._NO_PROXY, "必须每次给一份新的，别把常量递出去"

    # ⚠️ 解析整个模块再定位函数：`inspect.getsource` 取到的函数体自带缩进，
    #    直接 `ast.parse` 会 IndentationError。
    tree = ast.parse(inspect.getsource(c))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "proxy_sync")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "requests"]
    assert calls, "proxy_sync 里没找到 requests 调用（重构过？）"
    for call in calls:
        kwargs = {k.arg for k in call.keywords}
        assert "proxies" in kwargs, (
            f"第 {call.lineno} 行的 requests.{call.func.attr} 没传 proxies —— "
            f"Clash 没开时它会打不通本机 8002")


def test_orderbook_clients_do_not_trust_proxy_env():
    """订单簿撮合（:8001）走 httpx —— `trust_env` 默认 True，必须显式关掉。"""
    import api.routes.orderbook as ob
    from orderbook import market_maker as mm

    for mod in (ob, mm):
        tree = ast.parse(inspect.getsource(mod))
        clients = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in ("AsyncClient", "Client")]
        assert clients, f"{mod.__name__} 里没找到 httpx client（重构过？）"
        for call in clients:
            kwargs = {k.arg: k.value for k in call.keywords}
            assert "trust_env" in kwargs, (
                f"{mod.__name__}:{call.lineno} 的 httpx client 没写 trust_env=False —— "
                f"它会读 .env 里的 HTTP_PROXY，Clash 没开就连不上本机 8001")
            assert isinstance(kwargs["trust_env"], ast.Constant)
            assert kwargs["trust_env"].value is False


@pytest.mark.parametrize("url", ["http://localhost:8002", "http://127.0.0.1:8002"])
def test_requests_really_would_go_through_the_proxy_without_the_kwarg(url, monkeypatch):
    """证明这条门禁挡的不是假想问题：设了 HTTP_PROXY 之后，
    **不带 `proxies=` 的请求真的会去连代理端口**（而不是直连 localhost）。

    ⚠️ 用一个**必定没人监听**的端口当代理，所以断言的是「报的是代理连不上」，
    而不是「请求成功了」—— 后者会依赖本机真的跑着某个服务。
    """
    import requests

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")   # 9 = discard，没人 listen
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    with pytest.raises(requests.exceptions.ProxyError):
        requests.get(url, timeout=1)
