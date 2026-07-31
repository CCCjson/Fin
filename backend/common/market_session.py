"""「这个市场现在开着还是睡着」—— 四市场会话判定（S6）。

# 为什么要有这个模块

在此之前，全项目**只有一个**会话判定：`recommend_engine/session.py::_session_phase`，
而它**写死上海时间**（09:30–15:00 + 周末）。两个后果：

1. **闲打接口**：北京白天 US 是关着的，而 `price_alert_monitor` 每 30 秒轮一次 ——
   一天 2880 次 yfinance 请求砸在一个不会变的收盘价上，必被限流。
   （这个监控当初被关掉开机自启，就是因为「每轮要代理翻页数十次」。）
2. `00-PLAN §3` 裁决 18 要求「终端上要能一眼看出**这个市场现在是开着还是睡着**」，
   而这个判据根本不存在。

⚠️ **本模块不动 `recommend_engine/session.py`**：那个是 A 股专用且被 recommend 链路
依赖。这里把它变成一个特例，并用门禁钉死两者对 A 股同口径
（`tests/common/test_market_session.py`）。

⛔ **别把 A 股的会话假设套到 crypto 头上**：它 7×24，「收盘后不用刷新」对它永远不成立。
"""
from __future__ import annotations

from datetime import datetime, time

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK, normalize_market
from common.market_time import market_now

# 四态，与 `recommend_engine/session.py` 同名同义（那边是 A 股专用版）。
INTRADAY = "intraday"
PRE_MARKET = "pre_market"
AFTER_CLOSE = "after_close"
CLOSED_DAY = "closed_day"

# 各市场的连续交易时段（交易所本地时间）。
#
# ⚠️ **A 股这里刻意含午休**（11:30-13:00 算 intraday），与
# `recommend_engine/session.py` 保持一模一样 —— 那边的注释写着「午休按盘中用已成分钟线」。
# 改成排除午休会让 recommend 链路和本模块对同一时刻给出不同答案，比不准更糟。
#
# ⚠️ 港股同样含午休（12:00-13:00）。美股无午休。
_HOURS: dict[str, tuple[time, time]] = {
    A_SHARE: (time(9, 30), time(15, 0)),
    HK_STOCK: (time(9, 30), time(16, 0)),
    US_STOCK: (time(9, 30), time(16, 0)),
}

# ⚠️ **没有交易日历**：法定节假日会被当成交易日。与 `recommend_engine/session.py`
# 的取舍一致 —— 届时行情/信号自然为空，走观望，不影响正确性；
# 而引入一张要维护的节假日表，维护不上时反而会给出**自信的错误答案**。
_WEEKEND = (5, 6)


def session_phase(market: str | None, now: datetime | None = None) -> str:
    """这个市场当下处于哪个阶段。

    Args:
        now: 交易所**本地时区**的 datetime；不传则取当下。

    Returns:
        `intraday` / `pre_market` / `after_close` / `closed_day`。
        ⭐ **crypto 恒为 `intraday`** —— 它 7×24 没有休市，也没有周末。
    """
    m = normalize_market(market)
    if m == CRYPTO:
        return INTRADAY
    t = now or market_now(m)
    if t.weekday() in _WEEKEND:
        return CLOSED_DAY
    open_at, close_at = _HOURS[m]
    cur = t.time()
    if cur < open_at:
        return PRE_MARKET
    if cur < close_at:
        return INTRADAY
    return AFTER_CLOSE


def is_open(market: str | None, now: datetime | None = None) -> bool:
    """现在能不能拿到会变的价 —— **决定要不要发网络请求**的那个判据。"""
    return session_phase(market, now) == INTRADAY


def describe(market: str | None, now: datetime | None = None) -> str:
    """给人看的一句话（终端上「这个市场现在是开着还是睡着」）。"""
    phase = session_phase(market, now)
    return {
        INTRADAY: "交易中",
        PRE_MARKET: "尚未开盘",
        AFTER_CLOSE: "已收盘",
        CLOSED_DAY: "休市",
    }[phase]
