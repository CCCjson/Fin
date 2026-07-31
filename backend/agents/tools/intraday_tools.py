"""
盘中体检工具 —— 个股盘中的量比 / 分时VWAP / 涨速 / 日内位置。

定位：回答「现在追还是等回踩、挂什么价」的**时点**问题，只给结构化事实和
时点判读，不下买卖结论（该不该买归 recommend_stocks / get_cockpit_score）。
原料：pytdx 1 分钟线（现算）+ 东财定向快照（换手/振幅/昨收）。盘中专用。
"""
from typing import Any, Dict

from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope


# 盘中量能/VWAP/交易时段计算已下沉到 analysis_engine.indicators.intraday，
# 工具层保留同名别名，@tool 函数体照旧调用（薄适配）。
from analysis_engine.indicators.intraday import (  # noqa: E402
    avg5_volume as _avg5_volume,
    elapsed_trading_minutes as _elapsed_trading_minutes,
    intraday_vwap as _intraday_vwap,
)


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

    # 🔴 **这个工具整条链路都是 A 股专用的**（S6 复审发现）：
    #   `_session_phase` 写死上海 9:30-15:00、量比公式的分母 240 是 A 股全天分钟数
    #   （港股 330、crypto 1440）、分钟线走 pytdx（对港美股必空）。
    # S6 之前非沪深标的取不到价、工具诚实地拒绝；现在取得到了，**不拦就会出一份
    # 用 A 股模型算出来的、看起来很自信的错报告**。宁可拒绝。
    from common.market import A_SHARE, infer_market_from_symbol
    if infer_market_from_symbol(symbol) != A_SHARE:
        return ToolEnvelope(business_result="negative", message=(
            f"盘中体检目前只支持 A 股（{symbol} 不是）。它的量比/涨速/分钟线全都按"
            f"沪深交易时段和数据源算，套到港美股/crypto 上会给出**系统性错误**的数字。"
            f"这只票请用 get_cockpit_score 或 analyze_crypto。"))

    phase = _session_phase()
    if phase != "intraday":
        return ToolEnvelope(business_result="negative", message=(
            f"盘中体检是盘中专用工具（当前 {phase}）。收盘后看走势请用 "
            "get_daily_data，综合诊断用 get_cockpit_score。"))

    # 实时快照（现价/日内高低/累计量/昨收/换手/振幅）
    from acquisition.markets.realtime import fetch_quotes_by_symbols
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
        from acquisition.markets.pytdx_fetcher import PytdxFetcher
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
        # 优先用**源自报的报价时刻**；源没给才退回本机时钟，且明确标出来。
        # 此前恒为 now()：17:49 抓到的 15:34 收盘价会被标成 17:49，凭空说新两小时。
        "as_of": q.get("as_of") or now.strftime("%H:%M"),
        "as_of_is_fetch_time": q.get("as_of") is None,
        "source": q.get("source"),
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
        # `.get(k, 0)` 只在**键不存在**时兜底；键在而值是 None 会让 `:+.2f` 直接
        # TypeError。行情源没返回涨跌幅时该字段就是 None（quote_router 不再用 0 冒充
        # 缺失），所以这里必须显式判 None 而不是靠 get 的默认值。
        {"label": "现价",
         "value": (f"{price}（{_pct:+.2f}%）"
                   if (_pct := q.get("change_percent")) is not None else f"{price}（涨跌幅缺失）"),
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
