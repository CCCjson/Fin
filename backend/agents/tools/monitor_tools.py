"""
系统监控类工具 —— MoneyBill 的「monitor」身份核心。

get_system_pulse：一次性给出数据新鲜度、信号覆盖、追踪胜率、调度器状态 + 最近业务事件，
回答「系统还好吗 / 数据更新了吗 / 最近发生了啥」。limit 可调事件条数
（原 get_recent_events 已并入此工具，避免重复入口）。

复用 data_engine.health 的覆盖率/新鲜度 helper（与 api/routes/data_monitor.py 同源，
不再反向依赖路由层）。
"""
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.widgets import metric_cards_widget


def _recent_events(limit: int = 20, types=None):
    """读业务事件总线；Phase 2 未就绪时优雅降级为空。"""
    try:
        from business_events import BUS
        return BUS.recent(limit, types)
    except Exception:  # noqa: BLE001
        return []


def _signals_today_count(session) -> int:
    """今日生成的信号数（原 api/routes/data_monitor.py::_signals_today 已在页面重构时
    删掉，这两个概念不再属于"资产健康看板"，改成这里直接查 Signal 表）。"""
    from datetime import date
    from sqlalchemy import func
    from data_engine.storage.models import Signal
    return int(session.query(func.count(Signal.id)).filter(Signal.date == date.today()).scalar() or 0)


def _tracking_win_rate() -> "float | None":
    """信号追踪整体胜率（原 _tracking 同上已删，改用 SignalTracker 现成的聚合统计）。"""
    from analysis_engine.signal_tracker import SignalTracker
    tracker = SignalTracker()
    try:
        return tracker.get_strategy_stats()["overall"]["win_rate"]
    finally:
        tracker.close()


class GetSystemPulseArgs(BaseModel):
    limit: int = Field(5, ge=0, le=50, description="返回的业务事件条数，默认 5；想看更多动静就调大")


@tool(
    name="get_system_pulse",
    description=(
        "系统健康脉搏：行情数据是否最新、今日信号数、信号覆盖率、追踪胜率、每日流水线调度器状态，"
        "以及最近发生的业务事件（流水线完成/信号生成/下单成交/风控告警等，limit 可调条数）。"
        "用户问「系统正常吗 / 数据更新到几号 / 最近有啥动静 / 最近发生了什么」时调用。"
        "只看系统健康，不看盘面——市场行情/情绪用 get_market_pulse。"
    ),
    args_model=GetSystemPulseArgs,
    category="monitor",
    group="system",
)
def get_system_pulse(limit: int = 5) -> ToolEnvelope:
    from data_engine.health import get_coverage, get_freshness
    from data_engine.storage.database import get_session

    session = get_session()
    try:
        coverage = get_coverage()
        freshness = get_freshness(session)
        signal_count = _signals_today_count(session)
    finally:
        session.close()

    try:
        win_rate = _tracking_win_rate()
    except Exception:  # noqa: BLE001
        win_rate = None

    try:
        from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
        scheduler = daily_pipeline_scheduler.get_status()
    except Exception:  # noqa: BLE001
        scheduler = {}

    try:
        from news_engine.news_scheduler import news_scheduler
        news_sched = news_scheduler.get_status()
    except Exception:  # noqa: BLE001
        news_sched = {}

    events = _recent_events(max(0, min(int(limit or 5), 50)))
    summary = {
        "data_latest_date": freshness.get("latest_date"),
        "data_is_stale": freshness.get("is_stale"),
        "is_weekday": freshness.get("is_weekday"),
        "coverage": coverage.get("coverage_pct"),
        "signals_today": signal_count,
        "tracking_win_rate": win_rate,
        "scheduler_running": scheduler.get("running"),
        "scheduler_enabled": scheduler.get("enabled"),
        "news_scheduler_running": news_sched.get("running"),
        "news_scheduler_enabled": news_sched.get("enabled"),
        "news_last_run": news_sched.get("last_run"),
        "recent_events": [{"title": e.get("title"), "severity": e.get("severity"), "ts": e.get("ts")}
                          for e in events],
    }
    stale = freshness.get("is_stale")
    cards = [
        {"label": "数据最新日", "value": freshness.get("latest_date") or "无", "type": "neutral"},
        {"label": "今日数据", "value": ("待更新" if stale else "已到位"), "type": "risk" if stale else "quality"},
        {"label": "今日信号", "value": str(signal_count), "type": "neutral"},
        {"label": "追踪胜率", "value": (f"{win_rate}%" if win_rate is not None else "—"),
         "type": "quality"},
        {"label": "调度器", "value": ("运行中" if scheduler.get("running") else "停") + ("·开" if scheduler.get("enabled") else "·关"),
         "type": "neutral"},
        {"label": "Newnew新闻", "value": ("运行中" if news_sched.get("running") else "停") + ("·开" if news_sched.get("enabled") else "·关"),
         "type": "neutral"},
    ]
    widget = metric_cards_widget(cards, title="🩺 系统脉搏")
    return ToolEnvelope(data=summary, widget=widget)
