"""
盘中量能/VWAP/交易时段 + 大盘情绪判读下沉后的纯计算单测。

只测无需数据库/网络的纯函数（elapsed_trading_minutes / intraday_vwap /
mood_readout），验证下沉不改公式与阈值。
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import pandas as pd  # noqa: E402

from analysis_engine.indicators.intraday import (  # noqa: E402
    elapsed_trading_minutes,
    intraday_vwap,
)
from analysis_engine.market_mood import mood_readout  # noqa: E402


def _t(h, m):
    return datetime(2026, 7, 6, h, m)


def test_elapsed_trading_minutes_sessions():
    assert elapsed_trading_minutes(_t(9, 0)) == 0      # 开盘前
    assert elapsed_trading_minutes(_t(9, 30)) == 0     # 刚开盘
    assert elapsed_trading_minutes(_t(10, 0)) == 30
    assert elapsed_trading_minutes(_t(11, 30)) == 120  # 上午收
    assert elapsed_trading_minutes(_t(12, 0)) == 120   # 午休不计
    assert elapsed_trading_minutes(_t(13, 30)) == 150  # 下午 30 分钟
    assert elapsed_trading_minutes(_t(15, 0)) == 240   # 全天
    assert elapsed_trading_minutes(_t(16, 0)) == 240   # 收盘后封顶


def test_intraday_vwap_basic():
    df = pd.DataFrame({"volume": [100, 100], "amount": [1000, 1000]})
    # raw = 2000/200 = 10, 现价 10 落在 [5,20] → 直接采用
    assert intraday_vwap(df, 10.0) == 10.0


def test_intraday_vwap_hand_to_share_correction():
    df = pd.DataFrame({"volume": [10, 10], "amount": [10000, 10000]})
    # raw = 20000/20 = 1000，偏离现价 10 的 100 倍 → /100 = 10 落区间
    assert intraday_vwap(df, 10.0) == 10.0


def test_intraday_vwap_edge_cases():
    assert intraday_vwap(pd.DataFrame(), 10.0) is None                     # 空 df
    assert intraday_vwap(pd.DataFrame({"volume": [1]}), 10.0) is None      # 缺 amount 列
    assert intraday_vwap(pd.DataFrame({"volume": [100], "amount": [1000]}), 0) is None  # 无现价
    # raw 与 raw/100 都不落区间 → None
    df = pd.DataFrame({"volume": [1], "amount": [1]})
    assert intraday_vwap(df, 10.0) is None


def test_mood_readout_tones():
    indices = [{"name": "上证指数", "change_pct": 1.23}]
    up = mood_readout({"up": 3000, "down": 1000, "limit_up": 90, "limit_down": 2}, indices)
    assert "普涨" in up and "赚钱效应强" in up and "上证 +1.23%" in up

    down = mood_readout({"up": 800, "down": 3500, "limit_up": 5, "limit_down": 40}, [])
    assert "普跌" in down and "情绪冰点" in down

    mixed = mood_readout({"up": 2000, "down": 1800, "limit_up": 40, "limit_down": 3}, [])
    assert "涨跌分化" in mixed and "情绪一般" in mixed


def test_mood_readout_no_stats():
    assert mood_readout(None, []) == "全市场统计暂不可用"
    assert mood_readout({}, []) == "全市场统计暂不可用"
