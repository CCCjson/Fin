"""
数据类工具 —— 瘦封装 data_engine，供 MoneyBill 取行情/搜股。
引擎实例懒加载，结果裁剪成精简 summary 回灌 LLM。
"""
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from data_engine.engine import DataEngine
        _engine = DataEngine()
    return _engine


class SearchStocksArgs(BaseModel):
    keyword: str = Field(..., min_length=1, description="搜索关键字，如 '茅台' 或 '600519'")
    market: Literal["a_share", "hk_stock", "us_stock"] = Field("a_share", description="市场，默认 a_share")


@tool(
    name="search_stocks",
    description="按关键字（名称/代码/拼音）搜索股票，返回匹配的代码与名称。用户提到股票名但不确定代码时先用它。",
    args_model=SearchStocksArgs,
    category="data",
    group="core",
)
def search_stocks(keyword: str, market: str = "a_share") -> ToolEnvelope:
    results = _get_engine().search_stocks(keyword, market=market)
    top = results[:10]
    if not results:
        return ToolEnvelope(business_result="negative", message=f"没找到匹配「{keyword}」的股票")
    return ToolEnvelope(data={"count": len(results), "matches": top})


class GetDailyDataArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    window: int = Field(60, ge=1, le=3650, description="回看自然日天数，默认 60")


@tool(
    name="get_daily_data",
    description="获取个股最近一段时间的日线行情（OHLCV）摘要：最新价、区间涨跌幅、最高/最低、近几日走势。用于了解价格表现。",
    args_model=GetDailyDataArgs,
    category="data",
    group="core",
)
def get_daily_data(symbol: str, window: int = 60) -> ToolEnvelope:
    from agents.widgets import sparkline_widget
    end = datetime.now()
    start = end - timedelta(days=max(window, 5))
    df = _get_engine().get_daily_data(
        symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df is None or df.empty:
        return ToolEnvelope(business_result="negative",
                             message=f"未找到 {symbol} 在最近 {window} 天的日线数据")

    closes = df["close"]
    first, last = float(closes.iloc[0]), float(closes.iloc[-1])
    change_pct = round((last - first) / first * 100, 2) if first else None
    recent = []
    for idx, row in df.tail(5).iterrows():
        d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        recent.append({"date": d, "close": round(float(row["close"]), 3),
                       "volume": int(row["volume"]) if row.get("volume") is not None else None})

    # sparkline 用：近 60 根收盘价序列（旁路推前端，不进 LLM context）
    series = []
    for idx, row in df.tail(60).iterrows():
        d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        series.append({"date": d, "close": round(float(row["close"]), 3)})

    from data_engine.storage.database import get_session
    from data_engine.storage.repository import get_stock_names
    session = get_session()
    try:
        name = get_stock_names(session, [symbol]).get(symbol, symbol)
    finally:
        session.close()

    from common.context_quality import compute_quality
    from data_engine.quality_probe import daily_bars_block
    # scope 只有 daily_bars：本工具只查日线，不该因为「没有实时行情/技术面块」
    # 被扣分——那不是缺陷，是这个工具就不管那些。
    quality = compute_quality({"daily_bars": daily_bars_block(symbol)},
                              scope=["daily_bars"])

    summary = {
        "symbol": symbol,
        "name": name,
        "bars": int(len(df)),
        # **必须带日期**：字段名叫 latest 但值可能是一周前的收盘价。此前只给数字，
        # LLM 没有任何线索能看出它旧了（recent 里虽有日期，但那要它自己去比对今天几号）。
        "latest_close": round(last, 3),
        "latest_date": recent[-1]["date"] if recent else None,
        "change_pct": change_pct,
        "high": round(float(df["high"].max()), 3),
        "low": round(float(df["low"].min()), 3),
        "window_days": window,
        "recent": recent,
    }
    return ToolEnvelope(data=summary, widget=sparkline_widget(symbol, series, summary),
                        quality=asdict(quality))


class GetRealtimeQuoteArgs(BaseModel):
    symbols: list[str] = Field(..., min_length=1, description="股票代码列表，如 ['600519.SH','000001.SZ']")


@tool(
    name="get_realtime_quote",
    description=(
        "获取一只或多只股票的实时行情（最新价、涨跌幅等原始报价）。盘中想看当前报价时用。"
        "只报价不判读——盘面全景/市场情绪用 get_market_pulse，个股盘中强弱与买点时机用 get_intraday_check。"
    ),
    args_model=GetRealtimeQuoteArgs,
    category="data",
    group="core",
)
def get_realtime_quote(symbols: list[str]) -> ToolEnvelope:
    from agents.widgets import quote_widget
    from common.context_quality import compute_quality
    from data_engine.quality_probe import quote_block

    quotes, failures = _get_engine().get_realtime_quotes_with_status(symbols)
    quality = compute_quality({"quote": quote_block(quotes, symbols, failures)},
                              scope=["quote"])

    if not quotes:
        # 一只都没拿到时也要说清**为什么** —— 此前恒为「请稍后再试」，把
        # 代理额度耗尽 / 退市 / 市场不支持全收敛成同一句废话（重试根本没用）。
        why = ("；".join(f"{m}: {r}" for m, r in failures.items()) if failures
               else "全源均无这些标的（多半是退市/无效码，重试无用）")
        return ToolEnvelope(business_result="negative",
                            message=f"未获取到实时行情 —— {why}",
                            quality=asdict(quality))

    return ToolEnvelope(data={"count": len(quotes), "quotes": quotes},
                        widget=quote_widget(quotes), quality=asdict(quality))
