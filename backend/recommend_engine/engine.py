"""选股推荐引擎 —— recommend()

从 agents/tools/recommend_tools.py 下沉的完整业务流水线（纯位置迁移，行为不变）：
账户资金约束 → 持仓深度打分 → 候选双源合并去重 → 主板/ST/买得起预筛 →
盘中叠加 → 深度打分 top-N → 三重闸门 BUY 判定 → 持仓 SELL/HOLD 分流 → 决策留痕。

核心纪律（针对历史 bug 硬约束）：
1. 候选**只**来自真实数据（今日/最近信号 + 基本面选股器），绝不凭记忆报股票。
2. 持仓股走 SELL/HOLD 处置建议，未持仓的才可能给 BUY。
3. 只推「综合评级 BUY + 按账户买得起 + 过风控」的票，买不起的透明剔除到
   skipped_unaffordable，不硬凑。
4. 盘中用 pytdx 分钟线现算信号（不滞后，标 provisional）；今日无信号/弱市明确观望。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from common.market import A_SHARE
from common.market_time import market_today

from portfolio.calculator import PortfolioCalculator
from trading_engine.risk.adapter import get_total_capital, get_max_position_pct, build_broker_info

from recommend_engine import candidates as _cand
from recommend_engine import scoring as _scoring
from recommend_engine.session import _now_sh, _session_phase


def recommend(pool_id: Optional[str] = None, max_new_buys: int = 5,
              min_strength: float = 0.3) -> dict:
    """按今日行情选股：持仓分流 SELL/HOLD、非持仓推 BUY，受账户资金硬约束。

    Returns:
        完整 summary dict（含 buys / holdings_advice / skipped_unaffordable / note 等）。
    """
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
    signal_cands, signal_date, buy_count = _cand._fetch_signal_candidates(min_strength, held)
    screener_cands = _cand._fetch_screener_candidates(pool_id, held, limit=20)

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
    names = _cand._stock_names(all_syms)
    prices = _cand._latest_close_map(all_syms)

    skipped_unaffordable: list[dict] = []
    candidates: list[dict] = []
    for sym, c in merged.items():
        name = names.get(sym) or c.get("name") or sym
        c["name"] = name
        if not _cand._is_main_board(sym) or _cand._is_st(name):
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
        live = _cand._intraday_buy_map(scan_syms)
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
    agg = _scoring._aggregate_many(top_syms + list(held))

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
    is_today = signal_date == market_today(A_SHARE)
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
        from cockpit_engine.scorer import SCORER_VERSION
        from decision_log import record_decision
        for b in buys:
            record_decision(
                source="moneybill_recommend",
                symbol=b.get("symbol"),
                name=b.get("name"),
                action="BUY",
                recommendation="BUY",
                # 纯规则路径（无 LLM）：model_id 记打分器、prompt_version 记它的口径
                # 版本 —— 这条路走的就是 cockpit light 打分，所以复用 SCORER_VERSION。
                # 范式同 picks_log 的 model_id="rule:report_scorer"。
                model_id="rule:cockpit_light",
                prompt_version=SCORER_VERSION,
                # DecisionLog.confidence 的量纲是 0-100（见 models.py 该列注释：
                # 「驾驶舱综合分(0-100) 或其它置信度」）。这里曾经除以 100 存成 0-1，
                # 而 report_picks 存 0-100 —— 同一列两个量纲，跨 source 比胜率必错。
                confidence=b.get("composite") or None,
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

    return summary
