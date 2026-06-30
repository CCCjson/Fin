"""
数据类工具 —— 瘦封装 data_engine，供 MoneyBill 取行情/搜股。
引擎实例懒加载，结果裁剪成精简 summary 回灌 LLM。
"""
from datetime import datetime, timedelta

from agents.registry import tool

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from data_engine.engine import DataEngine
        _engine = DataEngine()
    return _engine


@tool(
    name="search_stocks",
    description="按关键字（名称/代码/拼音）搜索股票，返回匹配的代码与名称。用户提到股票名但不确定代码时先用它。",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "搜索关键字，如 '茅台' 或 '600519'"},
            "market": {"type": "string", "enum": ["a_share", "hk_stock", "us_stock"],
                       "description": "市场，默认 a_share"},
        },
        "required": ["keyword"],
    },
    category="data",
)
def search_stocks(keyword: str, market: str = "a_share") -> dict:
    results = _get_engine().search_stocks(keyword, market=market)
    top = results[:10]
    return {"summary": {"count": len(results), "matches": top}}


@tool(
    name="get_daily_data",
    description="获取个股最近一段时间的日线行情（OHLCV）摘要：最新价、区间涨跌幅、最高/最低、近几日走势。用于了解价格表现。",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码，如 600519.SH"},
            "days": {"type": "integer", "description": "回看自然日天数，默认 60"},
        },
        "required": ["symbol"],
    },
    category="data",
)
def get_daily_data(symbol: str, days: int = 60) -> dict:
    from agents.widgets import sparkline_widget
    end = datetime.now()
    start = end - timedelta(days=max(days, 5))
    df = _get_engine().get_daily_data(
        symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df is None or df.empty:
        return {"summary": f"未找到 {symbol} 在最近 {days} 天的日线数据"}

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

    summary = {
        "symbol": symbol,
        "bars": int(len(df)),
        "latest_close": round(last, 3),
        "change_pct": change_pct,
        "high": round(float(df["high"].max()), 3),
        "low": round(float(df["low"].min()), 3),
        "window_days": days,
        "recent": recent,
    }
    return {"summary": summary, "widget": sparkline_widget(symbol, series, summary)}


@tool(
    name="get_realtime_quote",
    description="获取一只或多只股票的实时行情（最新价、涨跌幅等）。盘中想看当前报价时用。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {"type": "array", "items": {"type": "string"},
                        "description": "股票代码列表，如 ['600519.SH','000001.SZ']"},
        },
        "required": ["symbols"],
    },
    category="data",
)
def get_realtime_quote(symbols: list[str]) -> dict:
    from agents.widgets import quote_widget
    quotes = _get_engine().get_realtime_quotes(symbols)
    out = {"summary": {"count": len(quotes), "quotes": quotes}}
    if quotes:
        out["widget"] = quote_widget(quotes)
    return out
