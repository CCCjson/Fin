"""域5 基线：C++ 信号驱动端点 /api/backtest/run_signals 的口径与自洽 parity。

第13步域5 阶段①给 C++ 服务加了「外部信号序列直接驱动撮合」的端点，作为
「回测统一 C++」的地基（后续 alpha_lab 迁到这里、Python backtest_engine 退役）。

这条基线做两件事：
1. 端点行为体检：次日开盘成交（防未来函数）、费用非零、metrics 字段集与 /run 一致。
2. 自洽 parity：把内置 MA_CROSS（/run）产出的成交反推成信号喂 /run_signals，
   两者 metrics 必须逐项对得上——证明信号回放走的是同一套引擎/撮合/指标口径。
   这也是阶段③ Python-vs-C++ parity 的基座。

标 integration：需要 backtest_server 跑在 :8002。服务不在 → skip 而非 fail。
重建服务：cd backtest_cpp/build && cmake --build . && ./backtest_server
"""
import json
import pathlib
import socket

import pytest
import requests

pytestmark = [pytest.mark.baseline, pytest.mark.integration]

CPP_URL = "http://localhost:8002"
FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TOLERANCE = 1e-6


def _cpp_alive() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 8002)) == 0


pytestmark.append(
    pytest.mark.skipif(not _cpp_alive(), reason="C++ 回测服务未跑在 :8002")
)


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads((FIXTURES / "cpp_parity_golden.json").read_text())


@pytest.fixture(scope="module")
def bars() -> list:
    return json.loads((FIXTURES / "cpp_bars.json").read_text())


@pytest.fixture(scope="module")
def ma_cross_result(golden, bars) -> dict:
    """内置 MA_CROSS 走 /run —— 作为反推信号的来源和 parity 对照。"""
    body = dict(golden["request"], bars=bars)
    resp = requests.post(f"{CPP_URL}/api/backtest/run", json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _post_signals(bars: list, signals: list, **overrides) -> dict:
    body = {
        "symbol": "TEST.SH",
        "market": "a_share",
        "initial_capital": 1_000_000.0,
        "start_date": "2024-01-02",
        "end_date": "2024-09-27",
        "bars": bars,
        "signals": signals,
    }
    body.update(overrides)
    resp = requests.post(f"{CPP_URL}/api/backtest/run_signals", json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ── 端点行为体检 ──

def test_run_signals_basic_shape(bars):
    """一买一卖：metrics 字段集与 /run 一致、strategy_name=SIGNAL、有资金曲线。"""
    buy_date = bars[30]["date"]
    sell_date = bars[60]["date"]
    result = _post_signals(bars, [
        {"date": buy_date, "action": "buy", "weight": 0.95},
        {"date": sell_date, "action": "sell"},
    ])

    assert result["strategy_name"] == "SIGNAL"
    assert len(result["equity_curve"]) == len(bars)
    assert "dropped_last_bar_orders" in result
    # metrics 字段集必须与 /run 的 golden 完全一致（口径统一的前提）
    golden_metrics_keys = set(json.loads(
        (FIXTURES / "cpp_parity_golden.json").read_text())["metrics"].keys())
    assert set(result["metrics"].keys()) == golden_metrics_keys


def test_run_signals_fills_next_bar_open_and_charges_fees(bars):
    """防未来函数 + 费率生效：成交=信号日次日开盘价、佣金/滑点非零。"""
    buy_date = bars[30]["date"]
    sell_date = bars[60]["date"]
    result = _post_signals(bars, [
        {"date": buy_date, "action": "buy"},
        {"date": sell_date, "action": "sell"},
    ])

    by_date = {b["date"]: b for b in bars}
    assert len(result["trades"]) == 2
    for trade in result["trades"]:
        bar = by_date[trade["date"]]
        # 成交价 ≈ 当日开盘价（±滑点），绝非某根收盘价
        assert abs(trade["price"] - bar["open"]) / bar["open"] < 0.02

    # 买信号在 bars[30] → 成交在 bars[31]
    assert result["trades"][0]["date"] == bars[31]["date"]
    assert result["metrics"]["total_commission"] > 0
    assert result["metrics"]["total_slippage"] > 0


def test_empty_signals_is_valid_flat(bars):
    """空信号合法：全程空仓，本金不变，无成交。"""
    result = _post_signals(bars, [])
    assert len(result["trades"]) == 0
    assert result["metrics"]["final_value"] == pytest.approx(1_000_000.0, abs=1e-6)


# ── 自洽 parity：MA_CROSS 反推信号 → run_signals，指标逐项对齐 ──

def test_signal_replay_matches_ma_cross(ma_cross_result, bars):
    """把 MA_CROSS 的成交反推成信号喂 run_signals，metrics 必须逐项一致。

    MA_CROSS(position_pct=0.95) 与 ExternalSignalStrategy(weight=0.95) 的
    仓位口径相同、都在信号日次日开盘成交，且现金演化一致 → 成交/指标应完全相同。
    这证明 run_signals 忠实回放、走的是同一套引擎口径。
    """
    date_index = {b["date"]: i for i, b in enumerate(bars)}

    # MA_CROSS 的每笔成交发生在「信号日的次日开盘」→ 信号日 = 成交 bar 的前一根
    signals = []
    for trade in ma_cross_result["trades"]:
        fill_idx = date_index[trade["date"]]
        assert fill_idx >= 1, "成交不该发生在首根 bar"
        signals.append({
            "date": bars[fill_idx - 1]["date"],
            "action": trade["side"].lower(),   # BUY/SELL → buy/sell
            "weight": 0.95,
        })

    replay = _post_signals(bars, signals)

    # 成交数一致
    assert len(replay["trades"]) == len(ma_cross_result["trades"])

    # metrics 逐项对齐（含 max_dd 日期等字符串字段）
    want = ma_cross_result["metrics"]
    got = replay["metrics"]
    assert set(got) == set(want)
    mismatches = []
    for key, w in want.items():
        g = got[key]
        if isinstance(w, str):
            if g != w:
                mismatches.append(f"{key}: {g!r} != {w!r}")
        elif abs(g - w) > TOLERANCE:
            mismatches.append(f"{key}: {g} != {w}")
    assert not mismatches, "run_signals 回放与 MA_CROSS 口径漂移:\n" + "\n".join(mismatches)
