"""域5 阶段②：alpha_lab signal_runner 的确定性验收（无需 LLM）。

signal_runner 是「AI 逐 bar 产信号 → C++ run_signals 执行+算指标」的核心。
本测试用一个固定的 on_bar（不打 LLM）验三件事：
1. collect_signals 只喂历史（防未来函数）+ 边沿去重成干净进出场事件。
2. normalize_metrics 把 C++ 小数口径转成 evaluator/iterate 期望的 schema/量纲。
3. backtest_via_cpp 端到端返回 evaluator 需要的全部键。

collect_signals/normalize_metrics 是纯函数（无需 C++）；backtest_via_cpp 需要
C++ 服务在 :8002 —— 复用探活-skip。
"""
import socket
import tempfile

import numpy as np
import pandas as pd
import pytest

from alpha_lab import signal_runner as sr

pytestmark = pytest.mark.integration


def _cpp_alive() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 8002)) == 0


def _make_df(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.sin(np.arange(n) / 5) + 0.1)
    df = pd.DataFrame(
        {"open": close - 0.2, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1e6},
        index=idx,
    )
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    return df


def _ma_cross(history: pd.DataFrame):
    if len(history) < 20:
        return None
    r = history.iloc[-1]
    if pd.isna(r["ma20"]):
        return None
    return "buy" if r["ma5"] > r["ma20"] else "sell"


# ── 纯函数：无需 C++ ──

def test_collect_signals_only_sees_history():
    """防未来函数结构自证：第 i 次调用只拿到长度 i+1 的历史。"""
    df = _make_df(30)
    seen = []

    def spy(history):
        seen.append(len(history))
        return None

    sr.collect_signals(spy, df)
    assert seen == list(range(1, 31))


def test_collect_signals_edge_dedup():
    """边沿去重：持仓中重复 buy、空仓 sell 都被忽略，只留干净进出场。"""
    df = _make_df(6)
    # 造 raw 动作序列：buy,buy,sell,sell,buy,None
    actions = iter(["buy", "buy", "sell", "sell", "buy", None])

    def scripted(history):
        return next(actions)

    signals = sr.collect_signals(scripted, df)
    # 期望：buy(第0根) → sell(第2根) → buy(第4根)
    assert [s["action"] for s in signals] == ["buy", "sell", "buy"]
    assert len(signals) == 3


def test_normalize_metrics_schema_and_scale():
    """C++ 小数口径 → evaluator/iterate schema：百分数 + 嵌套 max_drawdown + num_trades。"""
    cpp = {
        "sharpe_ratio": 1.5, "sortino_ratio": 2.0,
        "total_return": 0.25, "annualized_return": 0.30, "win_rate": 0.6,
        "profit_factor": 1.8, "total_trades": 7,
        "max_drawdown": 0.12, "max_drawdown_amount": 12000.0,
        "final_value": 1250000.0, "total_commission": 100.0, "total_slippage": 50.0,
    }
    m = sr.normalize_metrics(cpp)
    assert m["total_return"] == pytest.approx(25.0)          # ×100
    assert m["annualized_return"] == pytest.approx(30.0)
    assert m["win_rate"] == pytest.approx(60.0)
    assert m["num_trades"] == 7                               # total_trades→num_trades
    assert isinstance(m["max_drawdown"], dict)
    assert m["max_drawdown"]["max_drawdown_pct"] == pytest.approx(12.0)
    assert m["sharpe_ratio"] == pytest.approx(1.5)            # 裸值不变
    # evaluator + iterate 消费的键必须齐全
    for k in ("sharpe_ratio", "total_return", "annualized_return", "win_rate",
              "profit_factor", "num_trades", "max_drawdown"):
        assert k in m


def test_normalize_metrics_handles_garbage():
    """None/NaN/缺键 → 0，不炸。"""
    m = sr.normalize_metrics({"sharpe_ratio": None, "total_return": float("nan")})
    assert m["sharpe_ratio"] == 0.0
    assert m["total_return"] == 0.0
    assert m["num_trades"] == 0


# ── 端到端：需要 C++ 服务 ──

@pytest.mark.skipif(not _cpp_alive(), reason="C++ 回测服务未跑在 :8002")
def test_backtest_via_cpp_end_to_end():
    """固定 on_bar 走完整路径，train/val 都返回 evaluator 需要的全部键。"""
    df = _make_df(60)
    with tempfile.TemporaryDirectory() as d:
        tr, va = f"{d}/t.csv", f"{d}/v.csv"
        df.iloc[:42].to_csv(tr)
        df.iloc[42:].to_csv(va)

        mod = type("M", (), {"on_bar": staticmethod(_ma_cross)})
        res = sr.backtest_via_cpp(mod, tr, va, "TEST.SH", capital=1_000_000)

    assert res["success"], res.get("error")
    for side in ("train_metrics", "val_metrics"):
        m = res[side]
        for k in ("sharpe_ratio", "total_return", "annualized_return", "win_rate",
                  "profit_factor", "num_trades", "max_drawdown"):
            assert k in m, f"{side} 缺键 {k}"
        assert isinstance(m["max_drawdown"], dict)


@pytest.mark.skipif(not _cpp_alive(), reason="C++ 回测服务未跑在 :8002")
def test_backtest_via_cpp_missing_on_bar():
    """策略模块没有 on_bar → success=False，不炸。"""
    df = _make_df(30)
    with tempfile.TemporaryDirectory() as d:
        tr, va = f"{d}/t.csv", f"{d}/v.csv"
        df.iloc[:20].to_csv(tr)
        df.iloc[20:].to_csv(va)
        mod = type("Empty", (), {})
        res = sr.backtest_via_cpp(mod, tr, va, "TEST.SH")
    assert res["success"] is False
    assert "on_bar" in res["error"]
