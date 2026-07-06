"""
盘中体检工具 —— 个股盘中的量比 / 分时VWAP / 涨速 / 日内位置。

定位：回答「现在追还是等回踩、挂什么价」的**时点**问题，只给结构化事实和
时点判读，不下买卖结论（该不该买归 recommend_stocks / get_cockpit_score）。
原料：pytdx 1 分钟线（现算）+ 东财定向快照（换手/振幅/昨收）。盘中专用。
"""
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope


def _elapsed_trading_minutes(now) -> int:
    """今日已走过的交易分钟数（9:30-11:30 + 13:00-15:00，全天 240）。"""
    minutes = now.hour * 60 + now.minute
    morning = max(0, min(minutes - (9 * 60 + 30), 120))
    afternoon = max(0, min(minutes - 13 * 60, 120))
    return morning + afternoon


def _avg5_volume(symbol: str, today) -> Optional[float]:
    """近 5 个交易日的日均成交量（手，东财日线口径）。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import DailyQuote
    session = get_session()
    try:
        rows = (session.query(DailyQuote.volume)
                .filter(DailyQuote.symbol == symbol, DailyQuote.date < today)
                .order_by(DailyQuote.date.desc())
                .limit(5).all())
        vols = [float(r[0]) for r in rows if r[0]]
        return sum(vols) / len(vols) if vols else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取 {symbol} 近5日均量失败: {e}")
        return None
    finally:
        session.close()


def _intraday_vwap(df, current_price: float) -> Optional[float]:
    """分时均价 = 累计成交额 / 累计成交股数。

    pytdx 分钟线 vol 的单位（股/手）在不同市场历史上有出入，用现价做量级
    校验自适应：原始结果偏离现价 100 倍量级时按「手→股」修正。
    """
    if df.empty or "amount" not in df.columns or not current_price:
        return None
    vol_sum = float(df["volume"].sum())
    amt_sum = float(df["amount"].sum())
    if vol_sum <= 0 or amt_sum <= 0:
        return None
    raw = amt_sum / vol_sum
    for candidate in (raw, raw / 100.0):
        if 0.5 * current_price <= candidate <= 2.0 * current_price:
            return round(candidate, 3)
    return None


class GetIntradayCheckArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")


@tool(
    name="get_intraday_check",
    description=(
        "个股盘中体检（盘中专用）：现算量比、分时均价线VWAP（现价在其上方=盘中强势，"
        "回踩均价线可作挂单参考价）、近5/15分钟涨速、日内位置（是否创日内新高/新低）、"
        "换手率与振幅。回答「XX 现在追还是等回踩/现在买入时机如何/挂什么价」的时点问题。"
        "只给时点事实，不下买卖结论——该不该买用 recommend_stocks 或 get_cockpit_score。"
    ),
    args_model=GetIntradayCheckArgs,
    category="analysis",
    group="core",
)
def get_intraday_check(symbol: str) -> ToolEnvelope:
    from agents.tools.recommend_tools import _session_phase, _now_sh
    from agents.widgets import metric_cards_widget

    phase = _session_phase()
    if phase != "intraday":
        return ToolEnvelope(business_result="negative", message=(
            f"盘中体检是盘中专用工具（当前 {phase}）。收盘后看走势请用 "
            "get_daily_data，综合诊断用 get_cockpit_score。"))

    # 实时快照（现价/日内高低/累计量/昨收/换手/振幅）
    from data_engine.fetchers.realtime import fetch_quotes_by_symbols
    quotes = fetch_quotes_by_symbols([symbol])
    if not quotes or not quotes[0].get("price"):
        return ToolEnvelope(business_result="negative", message=f"{symbol} 实时行情获取失败，稍后再试。")
    q = quotes[0]
    price, high, low = q["price"], q.get("high"), q.get("low")

    now = _now_sh()
    today = now.date()

    # 当日 1 分钟线（pytdx 现算，含成交额）
    minute_df = None
    try:
        from data_engine.fetchers.pytdx_fetcher import PytdxFetcher
        fetcher = PytdxFetcher()
        df = fetcher.fetch_minute_bars(symbol, period=1, count=240, keep_amount=True)
        fetcher.close()
        if not df.empty:
            minute_df = df[df["date"].dt.date == today]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_intraday_check {symbol} 分钟线失败: {e}")

    summary: Dict[str, Any] = {
        "symbol": symbol, "name": q.get("name"),
        "price": price, "day_change_pct": q.get("change_percent"),
        "turnover_pct": q.get("turnover"), "amplitude_pct": q.get("amplitude"),
        "as_of": now.strftime("%H:%M"),
    }
    notes = []

    # 1) 分时 VWAP 与现价位置
    vwap = _intraday_vwap(minute_df, price) if minute_df is not None else None
    if vwap:
        above = price > vwap
        summary["vwap"] = vwap
        summary["price_vs_vwap_pct"] = round((price - vwap) / vwap * 100, 2)
        notes.append(f"现价在分时均价线{'上方' if above else '下方'}"
                     f"（VWAP {vwap}），{'盘中强势' if above else '盘中偏弱'}；"
                     f"等回踩可以 {vwap} 附近作挂单参考")

    # 2) 量比（今日节奏化累计量 vs 近5日均量）
    elapsed = _elapsed_trading_minutes(now)
    avg5 = _avg5_volume(symbol, today)
    today_vol = q.get("volume")
    if avg5 and today_vol and elapsed >= 5:
        volume_ratio = round(today_vol / (avg5 * elapsed / 240.0), 2)
        summary["volume_ratio"] = volume_ratio
        if volume_ratio >= 2:
            notes.append(f"量比 {volume_ratio}，明显放量")
        elif volume_ratio >= 1.2:
            notes.append(f"量比 {volume_ratio}，温和放量")
        elif volume_ratio <= 0.6:
            notes.append(f"量比 {volume_ratio}，显著缩量")
        else:
            notes.append(f"量比 {volume_ratio}，量能正常")

    # 3) 涨速（近 5 / 15 分钟）
    if minute_df is not None and len(minute_df) >= 6:
        closes = minute_df["close"].tolist()
        spd5 = round((closes[-1] / closes[-6] - 1) * 100, 2)
        summary["speed_5min_pct"] = spd5
        if len(closes) >= 16:
            summary["speed_15min_pct"] = round((closes[-1] / closes[-16] - 1) * 100, 2)
        if abs(spd5) >= 1:
            notes.append(f"近5分钟{'急涨' if spd5 > 0 else '急跌'} {spd5:+.2f}%，短线波动大")

    # 4) 日内位置
    if high and low and high > low:
        pos = round((price - low) / (high - low) * 100, 1)
        summary["day_range_position_pct"] = pos
        if price >= high * 0.999:
            notes.append("正创日内新高")
        elif price <= low * 1.001:
            notes.append("正处日内新低")
        else:
            notes.append(f"位于日内区间 {pos:.0f}% 分位（0=最低 100=最高）")

    summary["reading"] = "；".join(notes) if notes else "分钟线数据不足，仅有快照信息"

    cards = [
        {"label": "现价", "value": f"{price}（{q.get('change_percent', 0):+.2f}%）",
         "type": "neutral", "positive": (q.get("change_percent") or 0) >= 0},
        {"label": "分时均价", "value": str(vwap) if vwap else "—", "type": "neutral"},
        {"label": "量比", "value": str(summary.get("volume_ratio", "—")), "type": "neutral"},
        {"label": "5分钟涨速", "value": f"{summary.get('speed_5min_pct', 0):+.2f}%"
         if "speed_5min_pct" in summary else "—", "type": "neutral"},
        {"label": "日内位置", "value": f"{summary.get('day_range_position_pct', '—')}%",
         "type": "neutral"},
    ]
    return ToolEnvelope(
        data=summary,
        widget=metric_cards_widget(cards, title=f"⏱️ {q.get('name') or symbol} 盘中体检"))
