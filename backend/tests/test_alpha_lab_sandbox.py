"""
Alpha Lab 沙箱 AST 逃逸防护回归测试。

核心主张：LLM 生成的策略代码在 subprocess 执行前，AST 白名单必须挡住
经典沙箱逃逸链（dunder 属性爬回 subprocess/os/builtins）——因为 subprocess
本身无 OS 级隔离，一旦逃逸即等于用 Jason 的权限 RCE。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alpha_lab.sandbox import Sandbox  # noqa: E402


@pytest.fixture
def sb():
    return Sandbox()


# ──────────────────── 逃逸 payload 必须全部拦截 ────────────────────

ESCAPES = {
    "subclasses_chain": "x = ().__class__.__base__.__subclasses__()",
    "subclasses_call": (
        "for c in ().__class__.__base__.__subclasses__():\n"
        "    if c.__name__ == 'Popen': c(['id'])"),
    "globals_builtins": 'b = f.__init__.__globals__["__builtins__"]',
    "mro_walk": "t = (1).__class__.__mro__",
    "dict_access": "d = obj.__dict__",
    "import_os": "import os\nos.system('id')",
    "from_os_import": "from os import system",
    "getattr_bypass": "m = getattr(x, 'system')",
    "dunder_import": "__import__('os')",
    "pandas_to_csv": "df.to_csv('/tmp/x.csv')",
    "pandas_to_pickle": "df.to_pickle('/tmp/x.pkl')",
    "open_write": "f = open('/tmp/x', 'w')",
}


@pytest.mark.parametrize("name,code", list(ESCAPES.items()))
def test_escape_blocked(sb, name, code):
    safe, violations = sb.ast_check(code)
    assert not safe, f"逃逸 payload {name} 未被拦截！violations={violations}"
    assert violations


# ──────────────────── 合法策略不能误伤 ────────────────────

LEGIT_STRATEGY = '''
from backtest_engine.strategies.base import BaseStrategy, StrategyContext
import pandas as pd
import numpy as np


class GeneratedStrategy(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.name = "ma_cross"
        self.fast, self.slow = 5, 20

    def on_bar(self, ctx: StrategyContext):
        close = ctx.data["close"]
        fast = close.rolling(self.fast).mean()
        slow = close.rolling(self.slow).mean()
        if fast.iloc[-1] > slow.iloc[-1]:
            return 1
        elif fast.iloc[-1] < slow.iloc[-1]:
            return -1
        return 0
'''


def test_legit_strategy_passes(sb):
    safe, violations = sb.ast_check(LEGIT_STRATEGY)
    assert safe, f"合法策略被误伤！violations={violations}"


def test_super_init_allowed(sb):
    """super().__init__() 用到 __init__ 属性，必须放行（不能被 dunder 拦）。"""
    code = ("class GeneratedStrategy:\n"
            "    def __init__(self):\n"
            "        super().__init__()\n")
    safe, _ = sb.ast_check(code)
    assert safe


# ──────────────────── max_iterations 钳制 ────────────────────

@pytest.mark.parametrize("raw,expected", [
    (8, 8), (1, 1), (20, 20), (1000, 20), (0, 1), (-5, 1), ("bad", 8), (None, 8)])
def test_max_iter_clamp(raw, expected):
    args = {} if raw is None else {"max_iterations": raw}
    try:
        v = int(args.get("max_iterations", 8))
    except (TypeError, ValueError):
        v = 8
    v = max(1, min(v, 20))
    assert v == expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
