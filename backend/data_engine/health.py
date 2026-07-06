"""
数据健康度 —— 覆盖率 + 新鲜度检查。

从 api/routes/data_monitor.py 下沉：这两个函数本就是数据引擎的职责（覆盖率
转调 DailyUpdater，新鲜度直查 DailyQuote），不该让 agents/tools/monitor_tools.py
反向 import 路由层——route 和工具层现在都从这里取，消除那条反向依赖。
"""
from datetime import date, datetime

from sqlalchemy import func

from data_engine.storage.models import DailyQuote


def get_coverage() -> dict:
    """复用 DailyUpdater.get_update_status 的口径（total/latest/coverage/last_update）。"""
    from data_engine.daily_updater import DailyUpdater
    return DailyUpdater().get_update_status()


def get_freshness(session) -> dict:
    """行情数据最新日期 vs 今天，判断今日数据到没到。"""
    row = session.query(func.max(DailyQuote.date)).first()
    latest = row[0] if row and row[0] else None
    if isinstance(latest, str):
        try:
            latest = datetime.strptime(latest, "%Y-%m-%d").date()
        except ValueError:
            latest = None
    elif isinstance(latest, datetime):
        latest = latest.date()

    today = date.today()
    # 今天是否为工作日（周一~周五）——用于判断"应有当日数据却没到"
    is_weekday = today.weekday() < 5
    return {
        "latest_date": latest.isoformat() if latest else None,
        "today": today.isoformat(),
        "is_stale": (latest < today) if latest else True,
        "is_weekday": is_weekday,
    }
