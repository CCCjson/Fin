"""候选来源与小工具 —— 从 agents/tools/recommend_tools.py 原样下沉。

候选双源：
- 信号候选：signals 表最近交易日的 BUY 信号（HistoryRepository 同源）
- 选股器候选：screener_engine 基本面骨干（主板 + 去ST + 买得起 + ROE>=8）
- 盘中候选叠加：pytdx 分钟线 + 4 策略现算 BUY 强度
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from loguru import logger
from sqlalchemy import func

from data_engine.storage.database import get_session
from data_engine.storage.models import Signal, DailyQuote
from data_engine.storage.repository import get_stock_names


# ----------------------------------------------------------------------------
# 小工具：名称 / 主板 / 最新收盘价 / 信号原因解析
# ----------------------------------------------------------------------------
def _is_main_board(symbol: str) -> bool:
    """复用选股器口径：5000 本金只能交易沪深主板。"""
    from screener_engine.service import is_main_board as _mb
    return _mb(symbol)


def _stock_names(symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    session = get_session()
    try:
        return get_stock_names(session, symbols)
    finally:
        session.close()


def _latest_close_map(symbols: set[str]) -> dict[str, float]:
    """每只票的最新收盘价（DailyQuote max(date)）。"""
    if not symbols:
        return {}
    session = get_session()
    try:
        sub = session.query(
            DailyQuote.symbol, func.max(DailyQuote.date).label("md")
        ).filter(DailyQuote.symbol.in_(list(symbols))).group_by(DailyQuote.symbol).subquery()
        rows = session.query(DailyQuote.symbol, DailyQuote.close).join(
            sub, (DailyQuote.symbol == sub.c.symbol) & (DailyQuote.date == sub.c.md)
        ).all()
        return {sym: close for sym, close in rows}
    finally:
        session.close()


def _is_st(name: Optional[str]) -> bool:
    nm = (name or "").upper()
    return "ST" in nm or "退" in (name or "")


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


# ----------------------------------------------------------------------------
# 候选来源
# ----------------------------------------------------------------------------
def _fetch_signal_candidates(min_strength: float, held: set[str]) -> tuple[list[dict], Optional[date], int]:
    """从 signals 表取最近交易日的 BUY 信号候选（按 symbol 去重取 max strength）。

    Returns:
        (candidates, signal_date, buy_count_on_that_date)
    """
    session = get_session()
    try:
        latest = session.query(func.max(Signal.date)).scalar()
        if latest is None:
            return [], None, 0
        rows = session.query(Signal).filter(
            Signal.date == latest, Signal.signal_type == "BUY"
        ).all()
        best: dict[str, dict] = {}
        for r in rows:
            strength = float(r.strength or 0.0)
            if r.symbol in held or strength < min_strength:
                continue
            cur = best.get(r.symbol)
            if cur is None or strength > cur["strength"]:
                best[r.symbol] = {
                    "symbol": r.symbol,
                    "strength": strength,
                    "entry_price": r.entry_price,
                    "stop_loss": r.stop_loss,
                    "reasons": _parse_reasons(r.reasons),
                    "source": "signal",
                }
        cands = sorted(best.values(), key=lambda c: c["strength"], reverse=True)
        return cands, latest, len(rows)
    finally:
        session.close()


def _fetch_screener_candidates(pool_id: Optional[str], held: set[str], limit: int) -> list[dict]:
    """基本面选股器骨干：主板 + 去ST + 买得起 + ROE>=8，按 ROE 排。"""
    from screener_engine.service import run_screen as _run_screen, ScreenRequest, Filter
    try:
        rows = _run_screen(ScreenRequest(
            filters=[Filter(field="roe", op="gte", value=8)],
            pool_id=pool_id, sort_by="roe", sort_desc=True,
            affordable_only=True, exclude_st=True, main_board_only=True, limit=limit,
        ))
    except Exception as e:
        logger.warning(f"选股器候选获取失败: {e}")
        return []
    out = []
    for r in rows:
        if r["symbol"] in held:
            continue
        out.append({
            "symbol": r["symbol"],
            "name": r.get("name"),
            "price": r.get("price"),
            "roe": r.get("roe"),
            "strength": None,
            "reasons": [f"基本面：ROE {r.get('roe')}%"],
            "source": "screener",
        })
    return out


def _intraday_buy_map(symbols: list[str], period: int = 15, count: int = 64) -> dict[str, float]:
    """盘中：对候选跑 pytdx 分钟线 + 4 策略，返回 {symbol: 近端最大BUY强度}。

    只认最近 3 根K上触发的 BUY，逼近「此刻」。失败静默降级（返回已成功的部分）。
    """
    if not symbols:
        return {}
    import pandas as pd
    from acquisition.markets.pytdx_fetcher import PytdxFetcher
    from analysis_engine import AnalysisEngine
    from strategy.strategies import MACrossStrategy, MACDStrategy, KDJStrategy, RSIStrategy

    indicators = AnalysisEngine()
    strategies = [
        MACrossStrategy(fast_period=5, slow_period=20),
        MACDStrategy(), KDJStrategy(), RSIStrategy(),
    ]
    fetcher = PytdxFetcher()
    try:
        bars = fetcher.fetch_minute_bars_batch(symbols, period=period, count=count)
    except Exception as e:
        logger.warning(f"盘中分钟线获取失败，跳过盘中叠加: {e}")
        return {}
    finally:
        try:
            fetcher.close()
        except Exception:  # pragma: no cover
            pass

    live: dict[str, float] = {}
    for sym, df in bars.items():
        if df is None or df.empty:
            continue
        try:
            df = indicators.add_indicators(df)
            recent_dates = (
                set(pd.to_datetime(df["date"]).dt.date.iloc[-3:])
                if "date" in df.columns else set()
            )
            best = 0.0
            for st in strategies:
                for sig in st.generate_signals(df, sym):
                    if sig.signal_type.upper() != "BUY":
                        continue
                    # 只认最近几根K上的信号，逼近「此刻」
                    if recent_dates and getattr(sig, "date", None) not in recent_dates:
                        continue
                    best = max(best, float(sig.strength or 0.0))
            if best > 0:
                live[sym] = best
        except Exception as e:
            logger.warning(f"盘中信号计算 {sym} 失败: {e}")
    return live
