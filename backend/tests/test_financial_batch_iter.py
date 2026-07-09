"""
FinancialFetcher.fetch_batch_iter 并发路径的永久阻塞回归测试。

背景：2026-07-07 体检发现 `_one()` 里 `pool.acquire()` 在 try 外、
`result_q.put()` 不在 finally 里——只要 acquire() 本身抛异常（代理池耗尽/
超时等），该 symbol 就永远不会有结果入队，消费侧当年的定长
`for _ in range(len(symbols))` 无超时地等在 `result_q.get()` 上，永久阻塞。

这里验证修复后：无论 pool.acquire() 是否失败，迭代器都必然对每个 symbol
恰好产出一次终态结果，不会挂住；以及 fetch_batch 聚合器正确跳过心跳标记。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from net import proxy_pool as proxy_pool_mod  # noqa: E402
from data_engine.fetchers.financial import FinancialFetcher  # noqa: E402


class _AlwaysFailAcquirePool:
    """模拟旧 bug 触发条件：pool.acquire() 本身就会抛异常（代理池耗尽/超时）。"""

    def __init__(self, *args, **kwargs) -> None:
        self.direct_mode = False
        self.breaker_state = "closed"

    def acquire(self):
        raise RuntimeError("模拟代理池耗尽/超时")

    def release(self, slot) -> None:
        pass

    def report_success(self, slot) -> None:
        pass

    def report_failure(self, slot, proxy_connect: bool = False) -> None:
        pass


def test_fetch_batch_iter_survives_acquire_failure(monkeypatch):
    monkeypatch.setattr(proxy_pool_mod, "ProxyPool", _AlwaysFailAcquirePool)

    fetcher = FinancialFetcher()
    symbols = ["600000", "600001", "600002"]

    results = list(fetcher.fetch_batch_iter(symbols, workers=3))
    terminal = [(sym, df) for sym, df in results if sym is not None]

    # 回归点：即使 acquire() 每次都炸，也必须恰好产出 len(symbols) 个终态结果，
    # 而不是卡死在某个消费方等待 result_q.get() 的地方
    assert {sym for sym, _ in terminal} == set(symbols)
    assert len(terminal) == len(symbols)
    for _, df in terminal:
        assert df.empty


def test_fetch_batch_skips_heartbeat_entries(monkeypatch):
    fetcher = FinancialFetcher()

    def fake_iter(symbols, start_year="2015", workers=3):
        yield None, None  # 心跳标记，非终态结果
        yield "600000", pd.DataFrame({"a": [1]})
        yield None, None
        yield "600001", pd.DataFrame()  # 空结果视为失败

    monkeypatch.setattr(fetcher, "fetch_batch_iter", fake_iter)

    results = fetcher.fetch_batch(["600000", "600001"], workers=3)
    assert list(results.keys()) == ["600000"]
