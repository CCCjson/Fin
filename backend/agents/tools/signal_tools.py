"""
信号类工具 —— 纯查询：某日全市场信号、单股近期信号、信号胜率统计。

注意分工：signals 表是收盘后跑日线生成的，盘中有滞后；「今天买什么/帮我推荐」
类问题让位给 recommend_stocks（那边负责盘中现算 + 荐股纪律），这里只答
「触发了什么信号 / 某股最近信号如何 / 信号历史胜率」。
"""
import json
from datetime import date, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import func

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.turn_monitor import verdict_hook
from agents.widgets import metric_cards_widget
from data_engine.storage.database import get_session
from data_engine.storage.models import Signal, SignalTracking
from data_engine.storage.repository import get_stock_names as _stock_names


def _parse_reasons(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]
    except (json.JSONDecodeError, TypeError):
        return [str(raw)]


def _signal_row(s: Signal, names: dict[str, str]) -> dict:
    return {
        "symbol": s.symbol,
        "name": names.get(s.symbol),
        "signal_type": s.signal_type,
        "strength": round(float(s.strength or 0), 3),
        "price": s.price,
        "entry_price": s.entry_price,
        "stop_loss": s.stop_loss,
        "strategy": s.strategy,
        "reasons": _parse_reasons(s.reasons),
    }


class GetTodaySignalsArgs(BaseModel):
    signal_date: Optional[str] = Field(None, description="查询日期 YYYY-MM-DD；不填取最近有信号的交易日")
    signal_type: Optional[Literal["BUY", "SELL"]] = Field(None, description="可选：只看买入或卖出信号")
    limit: int = Field(20, ge=1, le=100, description="最多返回条数，默认 20，按强度降序")


@tool(
    name="get_today_signals",
    description=(
        "查某个交易日全市场触发的买卖信号（默认最近有信号的交易日），可按 BUY/SELL 过滤。"
        "纯查询秒回，回答「今天/某天有什么信号、触发了哪些买卖点」。"
        "注意：signals 表收盘后生成、盘中有滞后；「今天买什么/帮我推荐」请改用 recommend_stocks。"
    ),
    args_model=GetTodaySignalsArgs,
    category="analysis",
    group="signals",
)
def get_today_signals(signal_date: Optional[str] = None,
                      signal_type: Optional[str] = None, limit: int = 20) -> ToolEnvelope:
    limit = max(1, min(int(limit or 20), 100))
    session = get_session()
    try:
        if signal_date:
            try:
                target = date.fromisoformat(signal_date)
            except ValueError:
                return ToolEnvelope(business_result="negative",
                                     message=f"日期格式不对：{signal_date}，请用 YYYY-MM-DD")
        else:
            target = session.query(func.max(Signal.date)).scalar()
            if target is None:
                return ToolEnvelope(business_result="negative", message="signals 表还没有任何信号记录。")

        q = session.query(Signal).filter(Signal.date == target)
        if signal_type:
            q = q.filter(Signal.signal_type == signal_type.upper())
        rows = q.order_by(Signal.strength.desc()).all()

        buy_count = sum(1 for r in rows if r.signal_type == "BUY")
        sell_count = len(rows) - buy_count
        names = _stock_names(session, {r.symbol for r in rows[:limit]})
        signals = [_signal_row(r, names) for r in rows[:limit]]
    finally:
        session.close()

    is_today = target == date.today()
    note = None
    if not rows:
        note = (f"{target} 无{signal_type or ''}信号" if signal_date
                else "最近交易日无信号")
    elif not is_today:
        note = f"注意：这是 {target} 的信号（今日尚未生成，收盘后更新）"

    summary = {
        "signal_date": str(target),
        "is_today": is_today,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "returned": len(signals),
        "signals": signals,
        "note": note,
    }
    return ToolEnvelope(data=summary)


@verdict_hook("get_today_signals")
def _today_signals_verdict(s: str):
    """「某日无信号」是完整正常答案（休市日/收盘前未生成），不该进坏 streak。"""
    return "ok" if '"signal_date"' in s else None


class GetStockSignalsArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    days: int = Field(60, ge=1, le=365, description="回看最近 N 天，默认 60")


@tool(
    name="get_stock_signals",
    description=(
        "查单只股票近期触发过的买卖信号及每个信号的追踪表现（后续收益、是否止损、胜负判定）。"
        "回答「XX 最近有没有信号、上次信号后走势如何」。纯查询；荐股请用 recommend_stocks。"
    ),
    args_model=GetStockSignalsArgs,
    category="analysis",
    group="signals",
)
def get_stock_signals(symbol: str, days: int = 60) -> ToolEnvelope:
    days = max(1, min(int(days or 60), 365))
    cutoff = date.today() - timedelta(days=days)
    session = get_session()
    try:
        rows = (session.query(Signal)
                .filter(Signal.symbol == symbol, Signal.date >= cutoff)
                .order_by(Signal.date.desc()).limit(30).all())
        if not rows:
            return ToolEnvelope(business_result="negative", message=f"{symbol} 近 {days} 天没有触发过信号。")

        tracking = {
            t.signal_id: t
            for t in session.query(SignalTracking).filter(
                SignalTracking.symbol == symbol,
                SignalTracking.signal_date >= cutoff,
            ).all()
        }
        names = _stock_names(session, {symbol})
        signals = []
        for r in rows:
            item = _signal_row(r, names)
            item["date"] = str(r.date)
            t = tracking.get(r.signal_id)
            if t:
                item["tracking"] = {
                    "status": t.tracking_status,
                    "outcome": t.outcome,
                    "return_5d": t.return_5d,
                    "return_10d": t.return_10d,
                    "max_gain": t.max_gain,
                    "max_loss": t.max_loss,
                    "hit_stop_loss": bool(t.hit_stop_loss),
                }
            signals.append(item)
    finally:
        session.close()

    wins = sum(1 for s in signals if (s.get("tracking") or {}).get("outcome") == "win")
    losses = sum(1 for s in signals if (s.get("tracking") or {}).get("outcome") == "loss")
    summary = {
        "symbol": symbol,
        "name": names.get(symbol),
        "days": days,
        "count": len(signals),
        "win": wins,
        "loss": losses,
        "signals": signals,
    }
    return ToolEnvelope(data=summary)


class GetSignalStatsArgs(BaseModel):
    strategy: Optional[str] = Field(None, description="可选：只看某个策略，如 ma_cross")
    days: Optional[int] = Field(None, ge=1, le=3650, description="只统计最近 N 天的信号；不填统计全部")


@tool(
    name="get_signal_stats",
    description=(
        "信号历史胜率统计（总体 + 按策略）：胜/负/中性、5/10日均收益、MFE/MAE、止损止盈命中率。"
        "回答「金叉信号最近胜率如何 / 哪个策略靠谱」。"
    ),
    args_model=GetSignalStatsArgs,
    category="analysis",
    group="signals",
)
def get_signal_stats(strategy: Optional[str] = None, days: Optional[int] = None) -> ToolEnvelope:
    from analysis_engine.signal_tracker import SignalTracker
    tracker = SignalTracker()
    try:
        stats = tracker.get_strategy_stats(strategy=strategy, days=days)
    finally:
        tracker.close()

    overall = stats.get("overall", {})
    if not overall.get("total"):
        return ToolEnvelope(business_result="negative", message="没有符合条件的追踪信号记录。")

    cards = [
        {"label": "信号总数", "value": str(overall.get("total", 0)), "type": "neutral"},
        {"label": "胜率", "value": f"{overall.get('win_rate', 0)}%", "type": "quality",
         "positive": (overall.get("win_rate") or 0) >= 50},
        {"label": "5日均收益", "value": f"{overall.get('avg_return_5d')}%"
         if overall.get("avg_return_5d") is not None else "—", "type": "return",
         "positive": (overall.get("avg_return_5d") or 0) >= 0},
        {"label": "10日均收益", "value": f"{overall.get('avg_return_10d')}%"
         if overall.get("avg_return_10d") is not None else "—", "type": "return",
         "positive": (overall.get("avg_return_10d") or 0) >= 0},
        {"label": "止损命中", "value": f"{overall.get('stop_loss_hit_rate', 0)}%", "type": "risk"},
    ]
    title = f"信号胜率统计（{strategy or '全部策略'}" + (f"·近{days}天" if days else "") + "）"
    return ToolEnvelope(data=stats, widget=metric_cards_widget(cards, title=title))


class GenerateSignalsArgs(BaseModel):
    # symbols 数量上限(50)留给业务层处理（友好提示"请分批"），不在 schema 里硬拒。
    symbols: list[str] = Field(..., min_length=1, description="股票代码列表，如 [\"600519.SH\"]，最多 50 只")
    start_date: str = Field("", description="开始日期 YYYY-MM-DD，可选")
    end_date: str = Field("", description="结束日期 YYYY-MM-DD，可选")


@tool(
    name="generate_signals",
    description=(
        "【触发信号生成】对指定股票跑日线策略、生成买卖信号并入库。"
        "信号缺失/想立即重算某几只股票的信号时调用；纯查询用 get_today_signals。"
        "不填日期默认补算近 7 天。"
    ),
    args_model=GenerateSignalsArgs,
    category="analysis",
    group="signals",
    # 豁免确认门：虽然会写库（signals 表），但这是幂等重算——同参数重跑只会覆盖
    # 同一批交易日的信号，不产生资金/持仓副作用，也不会重复计费或误发指令。
    # 与 place_order/record_manual_trade 等真正改变资金状态的写操作不同，
    # 不需要人工二次确认这道闸门（Jason 拍板，见 memory/agent-arch-review）。
)
def generate_signals(symbols: list, start_date: str = "", end_date: str = "") -> ToolEnvelope:
    from strategy.signal_generator import SignalGenerator

    symbols = [s.strip().upper() for s in (symbols or []) if s and s.strip()]
    if not symbols:
        return ToolEnvelope(business_result="negative", message="缺少股票代码，无法生成信号。")
    if len(symbols) > 50:
        return ToolEnvelope(business_result="negative",
                             message=f"一次最多生成 50 只（收到 {len(symbols)}），请分批。")

    if not start_date or not end_date:
        end = date.today()
        start = end - timedelta(days=7)
        start_date = start_date or start.isoformat()
        end_date = end_date or end.isoformat()

    results = SignalGenerator().generate_signals_for_symbols(
        symbols=symbols, start_date=start_date, end_date=end_date, save_to_db=True,
    )
    total = sum(r.get("count", 0) for r in results.values())
    per_symbol = {
        sym: {"count": r.get("count", 0), "error": r.get("error")}
        for sym, r in results.items()
    }
    return ToolEnvelope(data={
        "window": f"{start_date} ~ {end_date}",
        "total_signals": total,
        "per_symbol": per_symbol,
        "note": "信号已入库，可用 get_stock_signals 查看明细。",
    })
