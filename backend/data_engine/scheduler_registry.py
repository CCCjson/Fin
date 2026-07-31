"""四个调度器的统一门面 —— 一次看全，逐个开关。

## 为什么需要它

后端里有四个各自独立的 APScheduler 单例：

    daily_pipeline_scheduler   A股主链 cron 15:35 周一~五 + 港美股独立 job 16:30
    crypto_scheduler           IntervalTrigger 每 30 分钟，7×24
    news_scheduler             每 15 分钟
    knowledge_scheduler        知识库

而前端只有**一个**开关，且只接到 `daily_pipeline_scheduler.set_enabled()`
（内部只增删 `JOB_ID` 这一个 job）。后果：

- **港美股 job 在界面上既看不到也关不掉** —— 它有独立 cron、独立 enabled 标志，
  `get_status()` 里明明返回了 `overseas_enabled` / `overseas_cron` /
  `overseas_last_run`，前端 TS 类型里连声明都没有。
- crypto、新闻两个调度器同理，完全不可见。

⛔ **刻意不合并这四个调度器**：它们的 trigger 语义真的不同（工作日 cron vs
7×24 interval vs 15 分钟），硬塞进一个只会让每个都别扭。这层只统一**展示和开关**。
"""
from __future__ import annotations

from collections.abc import Callable

from loguru import logger


def _daily_status() -> dict:
    from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
    s = daily_pipeline_scheduler.get_status()
    return {
        "id": "daily_a_share",
        "label": "A股主链",
        "description": "日线 → 补信号 → 更新追踪 → 涨停池 → 估值快照 → 决策后验",
        "enabled": s.get("enabled"),
        "running": s.get("running"),
        "is_updating": s.get("is_updating"),
        "cron": s.get("cron"),
        "next_run": s.get("next_run"),
        "last_run": s.get("last_run"),
        "toggleable": True,
    }


def _overseas_status() -> dict:
    """港美股是 `daily_pipeline_scheduler` 里的**独立 job**（独立 cron 16:30），
    不是主链的一步 —— 所以单列一行，而不是塞进 A 股那行的 chain 描述里。"""
    from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
    s = daily_pipeline_scheduler.get_status()
    next_run = None
    for job in s.get("jobs") or []:
        if job.get("id") == "overseas_daily_update":
            next_run = job.get("next_run")
            break
    return {
        "id": "overseas_daily",
        "label": "港美股日线",
        "description": "yfinance 批量增量。独立 16:30 触发（港股 16:00 才收盘）",
        "enabled": s.get("overseas_enabled"),
        "running": s.get("running"),
        "is_updating": s.get("overseas_is_updating"),
        "cron": s.get("overseas_cron"),
        "next_run": next_run,
        "last_run": s.get("overseas_last_run"),
        "toggleable": True,
    }


def _crypto_status() -> dict:
    from data_engine.crypto_scheduler import crypto_scheduler
    s = crypto_scheduler.get_status()
    return {
        "id": "crypto",
        "label": "Crypto 7×24",
        "description": f"币安现货增量 + 情报刷新，每 {s.get('interval_min')} 分钟一轮",
        "enabled": s.get("enabled"),
        "running": s.get("is_running"),
        "is_updating": s.get("is_updating"),
        "cron": f"每 {s.get('interval_min')} 分钟",
        "next_run": None,
        "last_run": None,
        # env 门控（CRYPTO_SCHEDULER_ENABLED），没有运行时 set_enabled
        "toggleable": False,
        "toggle_hint": "改 .env 的 CRYPTO_SCHEDULER_ENABLED 后重启后端",
    }


def _news_status() -> dict:
    from news_engine.news_scheduler import news_scheduler
    s = news_scheduler.get_status()
    return {
        "id": "news",
        "label": "新闻抓取",
        "description": f"抓取 + 情绪分析，每 {s.get('interval_minutes')} 分钟一轮",
        "enabled": s.get("enabled"),
        "running": s.get("running"),
        "is_updating": s.get("is_updating"),
        "cron": f"每 {s.get('interval_minutes')} 分钟",
        "next_run": s.get("next_run"),
        "last_run": s.get("last_run"),
        "toggleable": True,
    }


_PROVIDERS: dict[str, Callable[[], dict]] = {
    "daily_a_share": _daily_status,
    "overseas_daily": _overseas_status,
    "crypto": _crypto_status,
    "news": _news_status,
}


def all_scheduler_status() -> dict:
    """四个调度器的状态。单个查失败不影响其余（返回一个 error 行）。"""
    out = []
    for sid, provider in _PROVIDERS.items():
        try:
            out.append(provider())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[调度器] {sid} 状态查询失败: {e}")
            out.append({"id": sid, "label": sid, "enabled": None,
                        "running": None, "error": str(e), "toggleable": False})
    return {"schedulers": out}


def set_scheduler_enabled(scheduler_id: str, enabled: bool) -> dict:
    """逐个开关。不支持运行时开关的（crypto）会明确报错而不是假装成功。"""
    if scheduler_id == "daily_a_share":
        from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
        daily_pipeline_scheduler.set_enabled(enabled)
        return all_scheduler_status()
    if scheduler_id == "overseas_daily":
        from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
        daily_pipeline_scheduler.set_overseas_enabled(enabled)
        return all_scheduler_status()
    if scheduler_id == "news":
        from news_engine.news_scheduler import news_scheduler
        news_scheduler.set_enabled(enabled)
        return all_scheduler_status()
    if scheduler_id == "crypto":
        raise ValueError(
            "crypto 调度器只有 env 门控（CRYPTO_SCHEDULER_ENABLED），"
            "没有运行时开关。改 .env 后 `bash restart.sh --backend`"
        )
    raise KeyError(scheduler_id)
