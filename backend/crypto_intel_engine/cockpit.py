"""币版决策驾驶舱 —— 一个 symbol 进，一张完整分析卡出（对标股票 `get_cockpit_score`）。

「问一个币」= 「问一只股」：现价+走势 → 信号状态 → 综合分+买/卖/持有 → 入场/止盈/止损/
建议仓位 → 一句解释原因。**原料八成复用现成件**，这里只做「融合 + 编排」：

- 数据/指标/信号/技术打分：`DataEngine.get_daily_data` + `AnalysisEngine.add_indicators`
  + `SignalDetector.detect_all` + `CockpitAggregator._score_technical`（全市场无关，直接复用）。
- 择时三维打分 + 排雷否决闸融合：`crypto_intel_engine.scorer`（纯函数）。
- 价位：`crypto_dynamic_levels`（复用股票 ATR 那套但**倍数调大**=币专属更宽止损 + 价格自适应精度）。
- 仓位：`size_crypto_position`（币原生小数量，按 LOT_SIZE 取整 + MIN_NOTIONAL + 复用五条硬风控）。

只读、不下单。`broker_info` 给了（配了 key）才算具体建议仓位/金额；否则只给百分比。
"""
from datetime import datetime, timedelta
from typing import Any

from loguru import logger


# ── 价格自适应精度（镜像前端 marketDetect.priceDecimalsFor，小币不被 round(,2) 抹平）──
def _price_decimals(price: float) -> int:
    p = abs(price)
    if p >= 100:
        return 2
    if p >= 1:
        return 4
    if p >= 0.01:
        return 6
    return 8


def _rp(price: float | None) -> float | None:
    if price is None:
        return None
    return round(float(price), _price_decimals(price))


def crypto_dynamic_levels(entry_price: float | None, quotes: list[dict],
                          atr: float | None = None, signal_type: str = "BUY",
                          sl_atr_mult: float = 3.0,
                          tp_atr_mults: tuple[float, float, float] = (2.0, 4.0, 6.0)) -> dict | None:
    """币专属 ATR 止盈止损：默认**更宽止损**（3×ATR，股票是 2×）防高波动被噪声扫损。

    结构与 `report_engine.stock_analyzer.calculate_dynamic_levels` 一致（下游 scorer 按同样
    的键取 atr_stop_loss / take_profit_levels[level==2]），但倍数可调 + 价格按币值自适应精度。
    """
    if not entry_price or entry_price <= 0 or len(quotes) < 3:
        return None
    latest_close = quotes[-1].get("close", entry_price)
    if atr is None or atr <= 0:
        atr = latest_close * 0.04   # 加密波动大，缺 ATR 时默认按 4% 估（股票是 2%）

    if signal_type == "BUY":
        sl = entry_price - sl_atr_mult * atr
        trailing = latest_close - 1.5 * atr
        tps = [entry_price + m * atr for m in tp_atr_mults]
        risk, reward = entry_price - sl, tps[1] - entry_price
    else:
        sl = entry_price + sl_atr_mult * atr
        trailing = latest_close + 1.5 * atr
        tps = [entry_price - m * atr for m in tp_atr_mults]
        risk, reward = sl - entry_price, entry_price - tps[1]
    rr = round(reward / risk, 2) if risk > 0 else 0

    actions = ["减仓1/3", "减仓1/3", "清仓"]
    return {
        "atr_value": _rp(atr),
        "atr_stop_loss": _rp(sl),
        "trailing_stop": _rp(trailing),
        "take_profit_levels": [{"level": i + 1, "price": _rp(tps[i]), "action": actions[i]}
                               for i in range(3)],
        "risk_reward_ratio": rr,
        "sl_atr_mult": sl_atr_mult,
    }


def size_crypto_position(symbol: str, price: float | None, target_pct: float, *,
                         broker_info: dict, total_capital: float | None = None,
                         max_position_pct: float | None = None,
                         step_size: float | None = None, min_qty: float | None = None,
                         min_notional: float | None = None) -> dict:
    """把「目标仓位 %」换算成币原生下单量（小数，按 LOT_SIZE 取整 + MIN_NOTIONAL 校验）。

    对标 `trading_engine.position_sizing.size_position`，但币是小数量、无 100 股一手。
    硬约束：目标金额 ≤ 单标 20% 上限 ≤ 可用买力；最后过 `RiskManager` 五条硬规则。
    """
    from acquisition.markets.binance_trade import round_step
    from trading_engine.risk.adapter import (
        get_effective_risk_config,
        get_max_position_pct,
        get_total_capital,
    )
    from trading_engine.risk.manager import RiskManager

    if total_capital is None:
        total_capital = get_total_capital()
    if max_position_pct is None:
        max_position_pct = get_max_position_pct()
    cash = float(broker_info.get("cash") or 0.0)
    max_single = round(max_position_pct * total_capital, 2)
    warnings: list[str] = []
    result = {
        "target_pct": round(target_pct, 1), "total_capital": round(total_capital, 2),
        "available_cash": round(cash, 2), "max_single_amount": max_single,
        "quantity": 0.0, "amount_usdt": 0.0, "affordable": False,
        "risk_passed": False, "capped_by": None, "warnings": warnings,
    }
    if not price or price <= 0:
        warnings.append("无最新价，无法计算建议数量")
        return result
    if target_pct <= 0:
        result["capped_by"] = "score"      # 评分不建议买入
        return result

    target_amount = target_pct / 100.0 * total_capital
    hard_cap = min(max_single, cash)
    amount = min(target_amount, hard_cap)
    if amount <= 0:
        result["capped_by"] = "cash" if cash < max_single else "max_position_pct"
        warnings.append(f"可用买力不足：单标上限 ${max_single:,.0f} / 可用 ${cash:,.0f}")
        return result

    qty = round_step(amount / price, step_size)
    if min_qty and qty < min_qty:
        result["capped_by"] = "min_qty"
        warnings.append(f"数量 {qty} 低于最小下单量 {min_qty}")
        return result
    notional = qty * price
    if min_notional and notional < min_notional:
        result["capped_by"] = "min_notional"
        warnings.append(f"金额 ${notional:.2f} 低于最小名义额 ${min_notional}")
        return result
    if qty <= 0:
        result["capped_by"] = "cash"
        return result

    result.update({"quantity": qty, "amount_usdt": round(qty * price, 2), "affordable": True})
    if target_amount <= hard_cap:
        result["capped_by"] = "target"
    elif max_single <= cash:
        result["capped_by"] = "max_position_pct"
    else:
        result["capped_by"] = "cash"

    try:
        cfg = get_effective_risk_config()
        cfg["max_position_pct"] = max_position_pct
        cfg["max_total_position_pct"] = max(cfg.get("max_total_position_pct", 0.8), max_position_pct)
        passed, checks = RiskManager(cfg).check_order(
            symbol=symbol, action="BUY", quantity=qty, price=price, broker_info=broker_info)
        result["risk_passed"] = passed
        for c in checks:
            if not c.passed:
                warnings.append(f"[{c.severity}] {c.message}")
    except Exception as e:  # noqa: BLE001 — 风控异常不阻断展示，但标红
        logger.warning(f"size_crypto_position 风控校验异常 {symbol}: {e}")
        result["risk_passed"] = False
        warnings.append(f"风控校验异常: {e}")
    return result


def _pct_change(df, days: int) -> float | None:
    import pandas as pd
    if len(df) < days + 1:
        return None
    old = float(df.iloc[-(days + 1)]["close"])
    latest = float(df.iloc[-1]["close"])
    if old == 0 or pd.isna(old):
        return None
    return round((latest - old) / old * 100, 2)


def _build_reasons(dims: dict, screen: dict | None, reg_detail: dict) -> list[str]:
    """从各维明细拼几条「为什么」，供卡片/MoneyBill 直接引用（不靠 LLM 也有解释）。"""
    reasons: list[str] = []
    tech = (dims.get("technical") or {}).get("detail") or {}
    if tech.get("latest_signal_type"):
        reasons.append(f"技术信号：最新{tech['latest_signal_type']}（强度 {tech.get('strength')}）")
    reg = reg_detail or {}
    if reg.get("btc_regime"):
        zh = {"bull": "BTC 牛市（可做多波段）", "bear": "BTC 熊市（做多分被压低）",
              "unknown": "BTC 大势不明"}.get(reg["btc_regime"])
        reasons.append(zh)
    if isinstance(reg.get("fear_greed"), (int, float)):
        reasons.append(f"市场情绪 恐慌贪婪={reg['fear_greed']}")
    dv = (dims.get("derivatives") or {}).get("detail") or {}
    fr = dv.get("funding_rate")
    if isinstance(fr, (int, float)):
        tone = "多头付钱/偏热" if fr > 0 else "空头付钱/偏冷"
        reasons.append(f"资金费率 {fr * 100:.4f}%（{tone}）")
    if screen and screen.get("verdict") in ("avoid", "caution") and screen.get("flags"):
        reasons.append(f"排雷{screen['verdict']}：{screen['flags'][0]}")
    return reasons


def analyze_crypto_symbol(symbol: str, *, broker_info: dict | None = None,
                          current_position_pct: float = 0.0) -> dict:
    """一个币的完整分析卡。broker_info 给了（配 key）才算具体仓位/金额，否则只给百分比。"""
    from dataclasses import asdict

    import pandas as pd

    from advisor_engine.context_collector import AdvisorContextCollector
    from analysis_engine.engine import AnalysisEngine
    from analysis_engine.signals.detector import SignalDetector
    from cockpit_engine.aggregator import CockpitAggregator
    from crypto_intel_engine.regime import btc_regime
    from crypto_intel_engine.resolver import base_asset
    from crypto_intel_engine.scorer import (
        market_context,
        score_crypto_cockpit,
        score_derivatives,
        score_regime,
        screen_coin,
    )
    from data_engine.engine import DataEngine

    now = datetime.now()
    end = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=300)).strftime("%Y-%m-%d")
    base = base_asset(symbol)
    result: dict[str, Any] = {"symbol": symbol, "base_asset": base}

    # ① 行情 → 指标 → 信号 → 技术择时分
    df = None
    engine = None
    try:
        engine = DataEngine()
        raw = engine.get_daily_data(symbol, start, end, db_only=True)
        if raw is not None and not raw.empty:
            df = AdvisorContextCollector._normalize_df(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 行情读取失败 {symbol}: {e}")
    finally:
        if engine:
            engine.close()

    if df is None or df.empty:
        result["error"] = "库里暂无该币日线数据（可能未加入 universe 或未增量到）"
        result["recommendation"] = "N/A"
        return result

    latest_close = float(df.iloc[-1]["close"])
    result["price"] = {
        "latest": _rp(latest_close),
        "change_5d_pct": _pct_change(df, 5),
        "change_20d_pct": _pct_change(df, 20),
        "change_60d_pct": _pct_change(df, 60),
    }

    indicators = {}
    signals: list[dict] = []
    try:
        df_ind = AnalysisEngine().add_indicators(df)
        indicators = AdvisorContextCollector._extract_indicators(df_ind)
        signals = [s.to_dict() for s in SignalDetector(symbol).detect_all(df_ind)]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 指标/信号计算失败 {symbol}: {e}")
    tech_score, tech_detail = CockpitAggregator._score_technical(indicators, signals, latest_close)

    # ② 衍生品情绪
    deriv = {}
    try:
        from acquisition.markets.crypto_derivatives import get_derivatives_snapshot
        deriv = get_derivatives_snapshot(symbol)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 衍生品读取失败 {symbol}: {e}")
    dv_score, dv_detail = score_derivatives(deriv)

    # ③ 大势闸门
    reg = btc_regime()
    mctx = market_context()
    rg_score, rg_detail = score_regime(reg, mctx)

    # ④ 排雷（否决闸）
    try:
        screen = screen_coin(symbol)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 排雷失败 {symbol}: {e}")
        screen = {"verdict": "unknown", "score": None, "flags": ["排雷取数失败"]}

    # ⑤ 价位（币专属更宽止损）
    quotes = []
    for _, row in df.tail(20).iterrows():
        quotes.append({"open": float(row["open"]) if pd.notna(row["open"]) else 0,
                       "high": float(row["high"]) if pd.notna(row["high"]) else 0,
                       "low": float(row["low"]) if pd.notna(row["low"]) else 0,
                       "close": float(row["close"]) if pd.notna(row["close"]) else 0})
    levels = crypto_dynamic_levels(latest_close, quotes, indicators.get("atr"), "BUY")

    # ⑥ 数据质量 + 历史校准
    quality = None
    try:
        from common.context_quality import compute_quality
        from data_engine.quality_probe import daily_bars_block
        quality = compute_quality({"daily_bars": daily_bars_block(symbol)}, scope=["daily_bars"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 数据质量评估失败 {symbol}: {e}")
    try:
        from decision_log import get_calibration_factor
        calib = get_calibration_factor("crypto_cockpit")
    except Exception:  # noqa: BLE001
        calib = 1.0

    # ⑦ 融合打分（择时三维 + 排雷否决闸）
    from trading_engine.risk.adapter import get_max_position_pct
    max_pct = get_max_position_pct()
    dims = {"technical": tech_score, "derivatives": dv_score, "regime": rg_score}
    scored = score_crypto_cockpit(dims, screen, levels, current_position_pct,
                                  max_pct, quality, calib)

    # ⑧ 仓位（配 key 才具体）
    sizing = None
    if broker_info is not None and scored.get("suggested_position_pct"):
        try:
            from acquisition.markets.binance_trade import symbol_filters
            filt = symbol_filters(symbol)
            sizing = size_crypto_position(
                symbol, latest_close, scored["suggested_position_pct"],
                broker_info=broker_info, max_position_pct=max_pct,
                step_size=filt.get("step_size"), min_qty=filt.get("min_qty"),
                min_notional=filt.get("min_notional"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"crypto 仓位换算失败 {symbol}: {e}")

    result.update({
        "composite": scored["composite"],
        "recommendation": scored["recommendation"],
        "raw_composite": scored["raw_composite"],
        "calibration_factor": scored["calibration_factor"],
        "adjustments": list(scored["adjustments"]),
        "stop_loss": scored["stop_loss"],
        "take_profit": scored["take_profit"],
        "dynamic_levels": levels,
        "suggested_position_pct": scored["suggested_position_pct"],
        "suggested_add_pct": scored["suggested_add_pct"],
        "current_position_pct": scored["current_position_pct"],
        "suggested": sizing,
        "weights_used": scored["weights_used"],
        "available_dimensions": scored["available_dimensions"],
        "dimension_coverage": scored["dimension_coverage"],
        "dimensions": {
            "technical": {"score": tech_score, "detail": tech_detail},
            "derivatives": {"score": dv_score, "detail": dv_detail},
            "regime": {"score": rg_score, "detail": rg_detail},
        },
        "screen": {"score": screen.get("score"), "verdict": screen.get("verdict"),
                   "flags": screen.get("flags", [])},
        "data_quality": asdict(quality) if quality is not None else None,
        "derivatives_snapshot": deriv,
        "btc_regime": reg,
        "market_context": mctx,
        "reasons": _build_reasons(
            {"technical": {"detail": tech_detail}, "derivatives": {"detail": dv_detail}},
            screen, rg_detail),
    })
    return result
