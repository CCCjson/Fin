"""交易时段判断（A股，东八区）。

从 agents/tools/recommend_tools.py 下沉——这两个函数不仅选股引擎自己用，
market_tools / intraday_tools / portfolio_tools 也复用（recommend_tools.py 保留
re-export 供旧 import 路径继续工作）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from common.market import A_SHARE
from common.market_time import market_now


def _now_sh() -> datetime:
    """当前 A 股市场时区（Asia/Shanghai）时间。

    收口到 `common.market_time`（见 docs/CODING_STANDARDS.md §11）。原实现自己 import
    ZoneInfo，except 分支还会**静默退回本地时间**并注释「假设运行在东八区」——
    服务器一挪就悄悄错，而且错的是「现在算不算交易时段」这种没人会去核对的判断。
    """
    return market_now(A_SHARE)


def _session_phase(now: Optional[datetime] = None) -> str:
    """当前 A股 交易时段：intraday / pre_market / after_close / closed_day。

    注：无交易日历，仅按周末粗判；法定节假日会被当作交易日，
    但届时信号/分钟线为空会自然走观望，不影响正确性。
    """
    now = now or _now_sh()
    if now.weekday() >= 5:  # 周六/周日
        return "closed_day"
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 30:
        return "pre_market"
    if minutes < 15 * 60:  # 09:30–15:00（含午休，午休按盘中用已成分钟线）
        return "intraday"
    return "after_close"
