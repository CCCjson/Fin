"""
Walk-Forward 引擎单测（域9 下沉）——纯函数 + C++ 调用 mock，不依赖真实 C++/数据。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from alpha_lab import walk_forward as wf


def test_generate_windows_rolls_and_respects_end():
    windows = wf.generate_windows(
        "2020-01-01", "2021-06-30",
        train_months=12, test_months=3, step_months=3,
    )
    # 训练12+测试3=15个月一窗，步长3个月；末窗测试期不得越过 end
    assert len(windows) >= 1
    for w in windows:
        assert w["train_start"] < w["train_end"] < w["test_start"] < w["test_end"]
        assert w["test_end"] <= "2021-06-30"
    # 相邻窗训练起点按步长滚动
    if len(windows) >= 2:
        assert windows[0]["train_start"] < windows[1]["train_start"]


def test_generate_windows_empty_when_range_too_short():
    # 区间不够放下 12+3 个月 → 空窗（route 侧据此抛 400）
    windows = wf.generate_windows(
        "2020-01-01", "2020-06-30",
        train_months=12, test_months=3, step_months=3,
    )
    assert windows == []


def test_cpp_call_converts_market_to_wire_value():
    """命门：港股 canonical hk_stock 必须经 to_cpp_market → 'hk' 再发 C++（旧 route 的 bug）。"""
    captured = {}

    def _fake_proxy_sync(method, path, body, timeout=None):
        captured["body"] = body
        return {"metrics": {"sharpe_ratio": 1.0, "total_return": 0.1}}

    # 只验 market 转换 —— 但**必须喂真 bars**：取不到行情现在直接抛
    # （walk-forward 不许「不带 bars 发过去」让 C++ 造随机游走，
    #  见 tests/test_backtest_no_fake_data.py）
    import pandas as pd
    _bars = pd.DataFrame([{"date": "2020-01-02", "open": 10.0, "high": 11.0,
                           "low": 9.5, "close": 10.5, "volume": 1000.0}])

    with patch.object(wf, "proxy_sync", _fake_proxy_sync), \
         patch("data_engine.DataEngine") as _de:
        _de.return_value.get_daily_data.return_value = _bars
        wf._run_single_cpp_backtest(
            symbol="00700.HK", strategy="MA_CROSS", params={"fast": 5, "slow": 20},
            start_date="2020-01-01", end_date="2020-03-31",
            initial_capital=100000.0, market="hk_stock",
        )

    assert captured["body"]["market"] == "hk", "hk_stock 必须转成 C++ wire 值 'hk'"


def test_run_walk_forward_event_sequence():
    """事件序列 start → window_done×N → complete，字段形状与旧 route 一致。"""
    windows = [
        {"train_start": "2020-01-01", "train_end": "2020-12-31",
         "test_start": "2021-01-01", "test_end": "2021-03-31"},
    ]

    def _fake_single(*args, **kwargs):
        return {"metrics": {"sharpe_ratio": 1.2, "total_return": 0.15,
                            "max_drawdown": 0.08, "win_rate": 0.6, "total_trades": 10},
                "equity_curve": [{"date": "2021-01-01", "total_value": 100000}]}

    with patch.object(wf, "_run_single_cpp_backtest", _fake_single):
        events = list(wf.run_walk_forward(
            symbol="600000.SH", strategy="MA_CROSS", market="a_share",
            initial_capital=100000.0, param_grid={"fast": [5], "slow": [20]},
            windows=windows,
        ))

    assert events[0]["event"] == "start"
    assert events[0]["total_windows"] == 1
    assert events[1]["event"] == "window_done"
    assert events[1]["status"] == "completed"
    assert events[1]["test_sharpe"] == 1.2
    assert events[1]["test_return"] == 15.0  # 0.15 × 100
    assert events[-1]["event"] == "complete"
    assert events[-1]["completed_windows"] == 1
    assert "summary" in events[-1] and "oos_equity" in events[-1]
