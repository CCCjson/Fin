"""A 股交易时间判断 —— 从已删除的 automation/scheduler.py 抽出（那是 A 股自动交易调度器）。

仍被两个按需扫描器复用（都挂着 live MoneyBill 工具，非自动执行）：
`limit_up_scanner.py`（涨停池）、`position_guardian.py`（持仓守卫）。
"""
from datetime import datetime


def _is_trading_hours() -> bool:
    """判断当前是否在 A 股交易时间。"""
    now = datetime.now()
    # 周末不交易
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = (t >= datetime.strptime("09:30", "%H:%M").time() and
               t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("13:00", "%H:%M").time() and
                 t <= datetime.strptime("15:00", "%H:%M").time())
    return morning or afternoon
