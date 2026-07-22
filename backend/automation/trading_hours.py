"""A 股交易时间判断 —— 从已删除的 automation/scheduler.py 抽出（那是 A 股自动交易调度器）。

仍被两个按需扫描器复用（都挂着 live MoneyBill 工具，非自动执行）：
`limit_up_scanner.py`（涨停池）、`position_guardian.py`（持仓守卫）。
"""
from datetime import datetime

from common.market import A_SHARE
from common.market_time import market_now


def _is_trading_hours() -> bool:
    """判断当前是否在 A 股交易时间。

    时刻按 **A 股市场时区**取（`market_now(A_SHARE)`），不用服务器本地时间 ——
    「现在是不是盘中」这种判断错了不会报错，只会静默地在错误时段扫描/不扫描。
    """
    now = market_now(A_SHARE)
    # 周末不交易
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = (t >= datetime.strptime("09:30", "%H:%M").time() and
               t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("13:00", "%H:%M").time() and
                 t <= datetime.strptime("15:00", "%H:%M").time())
    return morning or afternoon
