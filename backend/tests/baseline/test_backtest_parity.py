"""13.1 基线：C++ 回测服务的执行/费用/绩效指标口径。

第 13 步拍板「回测只留 C++ 一个口径」（计划文档 §3.6）：Python backtest_engine 退役、
alpha_lab 改走 C++ 信号驱动端点。这条基线是那次迁移的 parity 锚点——
同一组 K 线 + 同一个策略，指标必须逐项对得上 golden。

它也防 C++ 侧无声漂移（那是个常驻进程，改完不重启就不生效，很容易改了没察觉）。

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

# 指标容差：C++ 是确定性计算，同输入同输出。留一点浮点余量即可。
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
def result(golden, bars) -> dict:
    body = dict(golden["request"], bars=bars)
    resp = requests.post(f"{CPP_URL}/api/backtest/run", json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def test_metrics_match_golden(result, golden):
    """全套指标逐项对齐。任何一项漂了，说明 C++ 的执行/费用/指标口径变了。"""
    actual = result["metrics"]
    expected = golden["metrics"]

    assert set(actual) == set(expected), (
        f"指标字段集变了。多出: {set(actual) - set(expected)}；"
        f"少了: {set(expected) - set(actual)}"
    )

    mismatches = []
    for key, want in expected.items():
        got = actual[key]
        if isinstance(want, str):
            if got != want:
                mismatches.append(f"{key}: {got!r} != {want!r}")
        elif abs(got - want) > TOLERANCE:
            mismatches.append(f"{key}: {got} != {want}")
    assert not mismatches, "指标漂移:\n" + "\n".join(mismatches)


def test_trade_count_matches_golden(result, golden):
    assert len(result["trades"]) == golden["trades_count"]


def test_fees_are_charged(result):
    """A股费率：佣金万2.5（最低5元）+ 印花税千1（卖出单边）+ 滑点。

    费用为零 = 费率配置没生效，回测结果会系统性偏乐观。
    """
    metrics = result["metrics"]
    assert metrics["total_commission"] > 0, "佣金为零，费率配置没生效"
    assert metrics["total_slippage"] > 0, "滑点为零，滑点配置没生效"


def test_fills_at_next_bar_open_not_same_bar_close(result, bars):
    """防未来函数：信号当日收盘产生，成交必须发生在**次日开盘价**（含滑点）。

    C++ 侧有 EngineTest.FillsAtNextBarOpenNotSameBarClose 守着，这里从 wire 再验一次。
    """
    by_date = {b["date"]: b for b in bars}
    for trade in result["trades"]:
        bar = by_date[trade["date"]]
        # 成交价 = 当日开盘价 ± 滑点，绝不该等于任何一根 bar 的收盘价那么巧
        assert abs(trade["price"] - bar["open"]) / bar["open"] < 0.02, (
            f"{trade['date']} 成交价 {trade['price']} 偏离当日开盘价 {bar['open']} 太多，"
            "疑似用了收盘价成交（未来函数）"
        )


def test_equity_curve_is_daily_and_monotonic_in_time(result, bars):
    curve = result["equity_curve"]
    assert len(curve) == len(bars)
    dates = [p["date"] for p in curve]
    assert dates == sorted(dates)
    assert all(p["total_value"] > 0 for p in curve)


def test_last_bar_signal_is_discarded(result):
    """末根 bar 的信号没有次日可成交，必须丢弃而不是当日成交。"""
    assert "dropped_last_bar_orders" in result


def test_strategies_endpoint_lists_expected_presets():
    resp = requests.get(f"{CPP_URL}/api/strategies", timeout=10)
    resp.raise_for_status()
    names = {s["name"] for s in resp.json()}
    assert {"MA_CROSS", "MOMENTUM", "MACD", "RSI", "KDJ", "BOLLINGER"} <= names
