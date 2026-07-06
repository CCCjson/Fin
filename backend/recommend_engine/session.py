"""交易时段判断（A股，东八区）。

从 agents/tools/recommend_tools.py 下沉——这两个函数不仅选股引擎自己用，
market_tools / intraday_tools / portfolio_tools 也复用（recommend_tools.py 保留
re-export 供旧 import 路径继续工作）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger


def _now_sh() -> datetime:
    """当前东八区时间（失败则退回本地时间，假设运行在东八区）。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception as e:  # pragma: no cover
        logger.warning(f"读取东八区时间失败，退回本地时间: {e}")
        return datetime.now()


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
