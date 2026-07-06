"""
选股推荐工具 —— recommend_stocks

按今日行情给 Jason 选股建议，核心纪律（针对历史 bug 硬约束）：
1. 候选**只**来自真实数据（今日/最近信号 + 基本面选股器），绝不凭记忆报股票。
2. 持仓股走 SELL/HOLD 处置建议，未持仓的才可能给 BUY。
3. 只推「综合评级 BUY + 按 5000 元账户买得起 + 过风控」的票，买不起的透明剔除到
   skipped_unaffordable，不硬凑。
4. 盘中用 pytdx 分钟线现算信号（不滞后，标 provisional）；今日无信号/弱市明确观望。

复用现成能力，不改引擎：
- 候选骨干：api.routes.screener._run_screen（affordable_only 天然只留买得起的主板股）
- 今日信号：data_engine 的 signals 表（HistoryRepository 同源）
- 盘中信号：strategy 的 pytdx + 指标 + 4 策略（同 SignalGenerator.scan_intraday 的零件）
- 深度评级/分流/可执行股数：cockpit_engine.CockpitAggregator.aggregate（内部已按持仓区分）
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from typing import Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.widgets import recommendation_board_widget
from data_engine.storage.database import get_session
from data_engine.storage.models import Signal, DailyQuote, Watchlist
from data_engine.storage.repository import get_stock_names
from portfolio.calculator import PortfolioCalculator
from trading_engine.risk.adapter import get_total_capital, get_max_position_pct, build_broker_info

# 盘中深度打分并发数（aggregate 偏 IO：新闻抓取 + ML 推理）
_AGG_WORKERS = 4


# ----------------------------------------------------------------------------
# 时段判断（A股，东八区）
# ----------------------------------------------------------------------------
def _now_sh() -> datetime:
    """当前东八区时间（失败则退回本地时间，假设运行在东八区）。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception as e:  # pragma: no cover
        logger.warning(f"读取东八区时间失败，退回本地时间: {e}")
        return datetime.now()


def _session_phase(now: Optional[datetime] = None) -> str:
    """当前 A股 交易时段：intraday / pre_market / after_close / closed_day。

    注：无交易日历，仅按周末粗判；法定节假日会被当作交易日，
    但届时信号/分钟线为空会自然走观望，不影响正确性。
    """
    now = now or _now_sh()
    if now.weekday() >= 5:  # 周六/周日
        return "closed_day"
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 30:
        return "pre_market"
    if minutes < 15 * 60:  # 09:30–15:00（含午休，午休按盘中用已成分钟线）
        return "intraday"
    return "after_close"


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
    from data_engine.fetchers.pytdx_fetcher import PytdxFetcher
    from strategy.indicators import TechnicalIndicators
    from strategy.strategies import MACrossStrategy, MACDStrategy, KDJStrategy, RSIStrategy

    indicators = TechnicalIndicators()
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
            df = indicators.calculate_all_indicators(df)
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


# ----------------------------------------------------------------------------
# 深度打分（aggregate）+ 并发
# ----------------------------------------------------------------------------
def _aggregate_safe(symbol: str) -> Optional[dict]:
    from cockpit_engine.aggregator import CockpitAggregator
    try:
        # light 档：跳过新闻情感 + ML 两个慢维度（批量选股用技术+基本面+持仓即可）
        return CockpitAggregator().aggregate(symbol, light=True)
    except Exception as e:
        logger.warning(f"深度打分 {symbol} 失败: {e}")
        return None


def _aggregate_many(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    results: dict[str, dict] = {}
    workers = min(_AGG_WORKERS, len(symbols))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sym, res in zip(symbols, pool.map(_aggregate_safe, symbols)):
            if res is not None:
                results[sym] = res
    return results


# ----------------------------------------------------------------------------
# 主工具
# ----------------------------------------------------------------------------
class RecommendStocksArgs(BaseModel):
    pool_id: Optional[Literal["sse50", "csi300", "csi500"]] = Field(
        None, description="可选：把候选限定在某指数池内；不填则全市场主板选股")
    max_new_buys: int = Field(5, ge=1, le=8, description="最多深度分析并推荐的新买入标的数，默认 5")
    min_strength: float = Field(0.3, ge=0, le=1, description="今日买入信号强度下限(0-1)，默认 0.3，过滤弱信号")


@tool(
    name="recommend_stocks",
    description=(
        "按今日行情给出选股建议。已持仓的给 SELL/HOLD 处置；未持仓且综合评级 BUY、"
        "按当前资金买得起、过风控的才给 BUY 推荐。盘中用分钟线实时算信号(标 provisional)，"
        "盘后用当日/最近日线信号；候选骨干来自基本面选股器(只留 5000 元买得起的主板股)。"
        "今日无信号/弱市时明确返回空仓观望，不硬凑、绝不凭记忆报股票。"
        "用户问「今天买什么/有啥可推荐/我的持仓要不要动/帮我选几只股」时调用。"
    ),
    args_model=RecommendStocksArgs,
    category="analysis",
    group="core",
)
def recommend_stocks(pool_id: Optional[str] = None, max_new_buys: int = 5,
                     min_strength: float = 0.3) -> ToolEnvelope:
    """按今日行情选股：持仓分流 SELL/HOLD、非持仓推 BUY，受 5000 元账户硬约束。"""
    max_new_buys = max(1, min(int(max_new_buys or 5), 8))
    phase = _session_phase()

    # ---- 账户资金约束 ----
    total_capital = get_total_capital()
    max_pct = get_max_position_pct()
    broker_info = build_broker_info(total_capital)
    cash = float(broker_info.get("cash") or 0.0)
    max_single = max_pct * total_capital
    afford_ceiling = min(max_single, cash)  # 一手成本必须 ≤ 此值

    # ---- 持仓（永远深度打分，给 SELL/HOLD）----
    positions = PortfolioCalculator().get_current_positions()
    held = {p["symbol"] for p in positions if (p.get("quantity") or 0) > 0}

    # ---- 候选：今日/最近信号 + 选股器骨干（都排除持仓）----
    signal_cands, signal_date, buy_count = _fetch_signal_candidates(min_strength, held)
    screener_cands = _fetch_screener_candidates(pool_id, held, limit=20)

    # 合并去重：信号候选优先（有「今日行情」依据），其次选股器
    merged: dict[str, dict] = {}
    for c in signal_cands + screener_cands:
        if c["symbol"] in merged:
            # 已有则补齐缺失字段（信号在前，保留其 strength）
            merged[c["symbol"]].setdefault("roe", c.get("roe"))
            continue
        merged[c["symbol"]] = dict(c)

    # 名称 / 主板 / ST 过滤 + 价格
    all_syms = set(merged)
    names = _stock_names(all_syms)
    prices = _latest_close_map(all_syms)

    skipped_unaffordable: list[dict] = []
    candidates: list[dict] = []
    for sym, c in merged.items():
        name = names.get(sym) or c.get("name") or sym
        c["name"] = name
        if not _is_main_board(sym) or _is_st(name):
            continue
        price = c.get("price") or prices.get(sym)
        c["price"] = price
        # 买得起预筛（跑 aggregate 之前就砍掉一手超上限的）
        if price:
            one_lot = price * 100
            if one_lot > afford_ceiling:
                skipped_unaffordable.append({
                    "symbol": sym, "name": name, "price": round(price, 2),
                    "one_lot_cost": round(one_lot, 0),
                    "reason": "max_position_pct" if one_lot > max_single else "cash",
                })
                continue
        candidates.append(c)

    # ---- 盘中叠加：只对将深评的 top-N 候选跑分钟线（pytdx 顺序拉，控数量控耗时）----
    # 拿「此刻」买入信号，标 provisional；对这 N 只内部按盘中强度重排。
    provisional = False
    intraday_note = None
    if phase == "intraday" and candidates:
        scan_syms = [c["symbol"] for c in candidates[:max_new_buys]]
        live = _intraday_buy_map(scan_syms)
        provisional = True
        if live:
            for c in candidates[:max_new_buys]:
                c["intraday_strength"] = live.get(c["symbol"])
            candidates[:max_new_buys] = sorted(
                candidates[:max_new_buys],
                key=lambda c: (c.get("intraday_strength") or 0, c.get("strength") or 0),
                reverse=True,
            )
        else:
            intraday_note = "盘中分钟线暂无新增买入信号触发"

    # ---- 深度打分：候选 top N + 全部持仓 ----
    top_syms = [c["symbol"] for c in candidates[:max_new_buys]]
    agg = _aggregate_many(top_syms + list(held))

    # 新买入桶：三重闸门（评级BUY + 买得起 + 过风控）
    cand_meta = {c["symbol"]: c for c in candidates}
    buys: list[dict] = []
    for sym in top_syms:
        r = agg.get(sym)
        if not r:
            continue
        rec = r.get("recommendation")
        sizing = r.get("suggested") or {}
        if rec != "BUY":
            continue  # 治「HOLD/SELL 塞进买入」
        if not sizing.get("affordable") or not sizing.get("risk_passed"):
            skipped_unaffordable.append({
                "symbol": sym, "name": r.get("name"),
                "price": (r.get("price") or {}).get("latest"),
                "one_lot_cost": round(((r.get("price") or {}).get("latest") or 0) * 100, 0),
                "reason": sizing.get("capped_by") or "risk",
            })
            continue
        meta = cand_meta.get(sym, {})
        buys.append({
            "symbol": sym, "name": r.get("name"),
            "composite": r.get("composite"), "recommendation": "BUY",
            "price": (r.get("price") or {}).get("latest"),
            "suggested": {
                "shares": sizing.get("shares"), "lots": sizing.get("lots"),
                "amount": sizing.get("amount"), "affordable": sizing.get("affordable"),
                "risk_passed": sizing.get("risk_passed"),
            },
            "stop_loss": r.get("stop_loss"),
            "strength": meta.get("intraday_strength") or meta.get("strength"),
            "reasons": meta.get("reasons", []),
        })
    buys.sort(key=lambda b: (b.get("composite") or 0), reverse=True)

    # 持仓桶：SELL/HOLD/BUY(加仓)
    holdings_advice: list[dict] = []
    for p in positions:
        sym = p["symbol"]
        r = agg.get(sym)
        if not r:
            holdings_advice.append({
                "symbol": sym, "name": p.get("name"), "composite": None,
                "recommendation": "N/A", "unrealized_pnl_pct": p.get("unrealized_pnl_pct"),
                "current_position": {"shares": p.get("quantity"),
                                     "value": p.get("market_value"), "pct": None},
                "stop_loss": None, "note": "评分数据不足",
            })
            continue
        holdings_advice.append({
            "symbol": sym, "name": r.get("name"),
            "composite": r.get("composite"), "recommendation": r.get("recommendation"),
            "unrealized_pnl_pct": p.get("unrealized_pnl_pct"),
            "current_position": r.get("current_position"),
            "stop_loss": r.get("stop_loss"),
        })

    # ---- 市场状态 & 说明 ----
    is_today = signal_date == date.today()
    if buys:
        market_state = "active"
    elif not candidates:
        market_state = "empty_signals"
    else:
        market_state = "weak"

    note_parts = []
    if signal_date and buy_count:
        tag = "今日" if is_today else f"最近交易日 {signal_date}"
        note_parts.append(f"{tag}全市场买入信号 {buy_count} 个")
    elif signal_date and not is_today:
        note_parts.append(f"今日尚无新增买入信号（最近信号日 {signal_date}）")
    else:
        note_parts.append("今日暂无买入信号")
    if provisional:
        note_parts.append("盘中信号基于实时分钟线、未走完，收盘可能变化")
        if intraday_note:
            note_parts.append(intraday_note)
    if market_state == "empty_signals":
        note_parts.append("无符合条件的可买候选，建议空仓观望")
    elif market_state == "weak":
        note_parts.append("候选无一达到 BUY(综合≥65)且买得起的门槛，建议观望")
    if skipped_unaffordable:
        note_parts.append(f"{len(skipped_unaffordable)} 只评级/基本面尚可但按 ¥{max_single:.0f} 单股上限买不起，已剔除")

    summary = {
        "as_of": _now_sh().isoformat(timespec="seconds"),
        "session_phase": phase,
        "market_state": market_state,
        "provisional": provisional,
        "total_capital": round(total_capital, 2),
        "available_cash": round(cash, 2),
        "max_single_amount": round(max_single, 2),
        "signal_date": str(signal_date) if signal_date else None,
        "signal_count_today": buy_count if is_today else 0,
        "candidates_after_filter": len(candidates),
        "deep_scored": len(top_syms),
        "scoring_basis": "技术+基本面+持仓（轻量档，未含新闻/ML；如需全维请对单只用体检）",
        "buys": buys,
        "holdings_advice": holdings_advice,
        "skipped_unaffordable": skipped_unaffordable[:20],
        "note": "；".join(note_parts),
    }
    logger.info(
        f"recommend_stocks: phase={phase} state={market_state} "
        f"buys={len(buys)} holdings={len(holdings_advice)} skipped={len(skipped_unaffordable)}"
    )

    # 决策留痕（provenance）：每条 BUY 推荐入 DecisionLog，供「上次推荐对不对」归因
    try:
        from decision_log import record_decision
        for b in buys:
            record_decision(
                source="moneybill_recommend",
                symbol=b.get("symbol"),
                name=b.get("name"),
                action="BUY",
                recommendation="BUY",
                confidence=(b.get("composite") or 0) / 100.0 or None,
                entry_price=b.get("price"),
                stop_loss=(b.get("stop_loss") or {}).get("price")
                if isinstance(b.get("stop_loss"), dict) else b.get("stop_loss"),
                reasons=b.get("reasons"),
                input_snapshot={"pool_id": pool_id, "min_strength": min_strength,
                                "session_phase": phase, "provisional": provisional,
                                "signal_date": str(signal_date) if signal_date else None},
                output_summary={"composite": b.get("composite"),
                                "suggested": b.get("suggested")},
                risk_passed=bool((b.get("suggested") or {}).get("risk_passed")),
            )
    except Exception:  # noqa: BLE001 — 留痕不可影响主流程
        pass

    # widget 用完整数据渲染前端看板；回灌 LLM 的 summary 用瘦身副本控 token
    widget = recommendation_board_widget(summary)
    slim = {
        **summary,
        "buys": [{**b, "reasons": (b.get("reasons") or [])[:3]} for b in buys],
        "skipped_unaffordable": skipped_unaffordable[:8],
    }
    return ToolEnvelope(data=slim, widget=widget)
