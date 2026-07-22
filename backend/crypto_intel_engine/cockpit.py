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


def signal_to_dict(sig: Any) -> dict:
    """`BaseSignal` dataclass → `_score_technical` 认的 dict 形状。

    ⚠️ 别改回 `s.to_dict()`：那是股票侧 `SignalGenerator` 产物的方法，而这里用的是
    `SignalDetector.detect_all` 返回的 `BaseSignal` **dataclass，它没有 to_dict**。
    抄错方法名会静默吞掉整批信号（技术维白白丢掉新鲜交叉的 ±8 分加成）。
    """
    stype = getattr(sig, "signal_type", None)
    return {
        "signal_type": getattr(stype, "value", str(stype or "")).upper(),
        "symbol": getattr(sig, "symbol", None),
        "date": getattr(sig, "timestamp", None),
        "price": getattr(sig, "price", None),
        "strength": getattr(sig, "strength", None),
        "reasons": getattr(sig, "reason", None),
        "indicators": getattr(sig, "indicators", None),
    }


def _safe_ctx(label: str, fn, *args):
    """取一项市场级上下文，失败返 None（上下文是加分项，不该拖垮整张分析卡）。"""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"上下文取数失败 {label}: {e}")
        return None


def _load_4h_bars(symbol: str, limit: int = 200) -> list[dict]:
    """取 4h K 线：库里（`crypto_bars`）优先，不够则实时拉币安。

    库优先是为了少打网（updater 每轮会补）；库里不够 60 根就回退实时拉——4h 线是
    「定扣扳机时机」用的，缺了整个多周期确认就废了，值得多打一次请求。
    """
    try:
        from crypto_intel_engine.store import read_bars
        from data_engine.storage.database import get_session
        session = get_session()
        try:
            bars = read_bars(session, symbol, interval="4h", limit=limit)
        finally:
            session.close()
        if len(bars) >= 60:
            return bars
    except Exception as e:  # noqa: BLE001 — 表不存在/查询失败，回退拉网
        logger.debug(f"4h 线读库失败 {symbol}: {e}")
    try:
        from acquisition.markets.crypto import CryptoFetcher
        return CryptoFetcher().fetch_bars(symbol, interval="4h", limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"4h 线拉取失败 {symbol}: {e}")
        return []


def score_timeframe_alignment(daily_score: float | None, bars_4h: list[dict]) -> tuple[float | None, dict]:
    """日线定方向、4h 定扣扳机 —— 两个周期一致就加分，背离就打折。

    策略每 30 分钟 tick 却只看日线，是 7×24 市场上的结构错配：日线一天才变一次，
    高频空转毫无新信息。4h 线补上这段分辨率。

    判定用 4h 的 MA10/MA30 关系（快慢线）：**技术分是「看多程度」，所以 4h 永远把分数往
    它自己的方向推** —— 4h 走多加分（最多 +8），4h 走空扣分（最多 -8）。

    ⛔ 不能用「方向是否一致」当加减号：那样日线看空 + 4h 也看空会算成「同向」而**加分**，
    4h 确认下跌反倒把技术分从 46 抬到 54（跨过 50 中轴），正好在这个过滤器本该否决的
    形态上帮倒忙。`aligned` 只留作展示口径（描述两个尺度是否讲同一个故事）。
    """
    if daily_score is None or len(bars_4h) < 30:
        return daily_score, {"available": False, "reason": "4h 样本不足"}

    closes = [b["close"] for b in bars_4h if b.get("close")]
    if len(closes) < 30:
        return daily_score, {"available": False, "reason": "4h 样本不足"}

    fast = sum(closes[-10:]) / 10
    slow = sum(closes[-30:]) / 30
    tf_bullish = fast > slow
    daily_bullish = daily_score >= 50

    from common.scoring_utils import clamp
    gap_pct = (fast - slow) / slow * 100 if slow else 0.0
    strength = clamp(abs(gap_pct) * 2.0, 0, 8)
    aligned = tf_bullish == daily_bullish
    adj = strength if tf_bullish else -strength      # 按 4h 自己的方向推，不按是否同向
    return round(clamp(daily_score + adj, 0, 100), 1), {
        "available": True,
        "aligned": aligned,
        "tf_4h_trend": "bullish" if tf_bullish else "bearish",
        "daily_trend": "bullish" if daily_bullish else "bearish",
        "ma_gap_pct": round(gap_pct, 3),
        "adjustment": round(adj, 1),
        "bars": len(closes),
    }


def _spot_flow_from_bars(bars_4h: list[dict]) -> dict:
    """从 4h 线算近 24h 的现货买压与成交额（零额外请求——K 线里本来就带）。"""
    recent = [b for b in bars_4h[-6:] if b.get("taker_buy_ratio") is not None]
    out: dict[str, Any] = {}
    if recent:
        out["spot_taker_buy_ratio"] = sum(b["taker_buy_ratio"] for b in recent) / len(recent)
    quotes = [b.get("quote_volume") for b in bars_4h[-6:] if b.get("quote_volume")]
    if quotes:
        out["quote_volume_24h"] = sum(quotes)
    return out


def _build_reasons(dims: dict, screen: dict | None, reg_detail: dict) -> list[str]:
    """从各维明细拼几条「为什么」，供卡片/MoneyBill 直接引用（不靠 LLM 也有解释）。"""
    reasons: list[str] = []
    tech = (dims.get("technical") or {}).get("detail") or {}
    if tech.get("latest_signal_type"):
        reasons.append(f"技术信号：最新{tech['latest_signal_type']}（强度 {tech.get('strength')}）")
    tf = tech.get("timeframe_4h") or {}
    if tf.get("available"):
        tf_bull = tf.get("tf_4h_trend") == "bullish"
        if tf.get("aligned"):
            reasons.append("日线与 4h 同向走多（扣扳机时机较好）" if tf_bull
                           else "⚠️ 日线与 4h 同向走空（4h 确认下跌，别接飞刀）")
        else:
            reasons.append(f"⚠️ 4h 与日线背离（4h {'走多' if tf_bull else '走空'}，"
                           f"{'反弹待确认' if tf_bull else '追进去易挨回调'}）")
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
    dv2 = (dims.get("derivatives") or {}).get("detail") or {}
    oi = dv2.get("oi_price") or {}
    if oi.get("label"):
        reasons.append(f"杠杆资金：{oi['label']}")

    fl = (dims.get("flow") or {}).get("detail") or {}
    if isinstance(fl.get("stablecoin_change_pct"), (int, float)):
        sc = fl["stablecoin_change_pct"]
        reasons.append(f"稳定币水位 30 日{sc:+.2f}%（{'新钱进场' if sc > 0 else '资金在撤'}）")
    if isinstance(fl.get("spot_taker_buy_ratio"), (int, float)):
        tb = fl["spot_taker_buy_ratio"]
        reasons.append(f"现货主动买入占比 {tb:.1%}（{'买方主动' if tb > 0.5 else '卖方主动'}）")
    if fl.get("category_name") and isinstance(fl.get("category_change_24h"), (int, float)):
        reasons.append(f"所属赛道「{fl['category_name']}」24h {fl['category_change_24h']:+.2f}%")
    if fl.get("thin_liquidity"):
        reasons.append("⚠️ 盘口偏薄，滑点不可控")

    sn = (dims.get("sentiment") or {}).get("detail") or {}
    if sn.get("available"):
        reasons.append(f"新闻情绪 {sn['net_sentiment']:+.2f}（{sn['article_count']} 篇）")

    if screen and screen.get("hard_events"):
        reasons.append(f"⛔ 事件否决：{screen['hard_events'][0].get('label')}")
    elif screen and screen.get("verdict") in ("avoid", "caution") and screen.get("flags"):
        reasons.append(f"排雷{screen['verdict']}：{screen['flags'][0]}")
    # 「查过了没问题」与「压根没查」必须能区分——否则昨天刚宣布下架的币会被静默放行
    if screen and screen.get("hard_events_checked") is False:
        reasons.append("⚠️ 事件否决闸未执行（币安公告源不可用），下架/被盗类风险本次未排查")
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
    from crypto_intel_engine import context as ctx
    from crypto_intel_engine.regime import btc_regime
    from crypto_intel_engine.resolver import base_asset
    from crypto_intel_engine.scorer import (
        CRYPTO_SCORER_VERSION,
        market_context,
        score_crypto_cockpit,
        score_derivatives,
        score_flow,
        score_regime,
        score_sentiment,
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
        signals = [signal_to_dict(s) for s in SignalDetector(symbol).detect_all(df_ind)]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 指标/信号计算失败 {symbol}: {e}")
    tech_daily, tech_detail = CockpitAggregator._score_technical(indicators, signals, latest_close)

    # ①b 4h 多周期确认：日线定方向，4h 定扣扳机（修「30 分钟 tick 吃日线」的结构错配）
    bars_4h = _load_4h_bars(symbol)
    tech_score, tf_detail = score_timeframe_alignment(tech_daily, bars_4h)
    tech_detail = {**(tech_detail or {}), "daily_score": tech_daily, "timeframe_4h": tf_detail}

    # ② 衍生品情绪（带历史 → 资金费率走分位而非写死阈值）
    deriv = {}
    try:
        from acquisition.markets.crypto_derivatives import get_derivatives_snapshot
        deriv = get_derivatives_snapshot(symbol)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 衍生品读取失败 {symbol}: {e}")
    funding_hist = [r["funding_rate"] for r in (deriv.get("funding_history") or [])
                    if isinstance(r.get("funding_rate"), (int, float))]
    # OI×价格象限必须在**同一窗口**上比：oi_history 是 30 个日度点（见 crypto_derivatives
    # 的 limit=30），此前却喂 change_20d_pct —— OI 涨 30 天、价格只跌了最近 20 天的币会被
    # 判进错误象限（「空头堆积」vs「新钱做多」差 40 分），错误的中文标签还原样进 reasons。
    oi_days = len(deriv.get("oi_history") or [])
    dv_score, dv_detail = score_derivatives(
        deriv, funding_history=funding_hist,
        price_change_pct=(_pct_change(df, oi_days) if oi_days >= 2 else None))

    # ③ 大势闸门（BTC 主导率趋势 + 宏观风险偏好）
    reg = btc_regime()
    mctx = market_context()
    dom_chg = _safe_ctx("主导率趋势", ctx.dominance_change_pct)
    macro = _safe_ctx("宏观", ctx.macro_context)
    rg_score, rg_detail = score_regime(reg, mctx, dominance_change_pct=dom_chg,
                                       macro=macro, is_btc=(base == "BTC"))

    # ④ 排雷（否决闸）—— 真解锁日程 + 链上 TVL + 盘口厚度 + 事件硬否决
    spot_flow = _spot_flow_from_bars(bars_4h)
    from crypto_intel_engine.scorer import THIN_LIQUIDITY_USDT
    qv = spot_flow.get("quote_volume_24h")
    extras = {
        "tvl_change_pct": _safe_ctx("TVL 趋势", ctx.chain_tvl_change_pct, base),
        "thin_liquidity": (qv is not None and qv < THIN_LIQUIDITY_USDT),
    }
    try:
        screen = screen_coin(symbol, extras=extras)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 排雷失败 {symbol}: {e}")
        screen = {"verdict": "unknown", "score": None, "flags": ["排雷取数失败"],
                  "hard_events_checked": False}

    # ④b 资金流维（稳定币水位 + 现货主动买盘 + 叙事轮动）
    cat_name, cat_chg = ctx.pick_category(screen.get("categories"))
    flow_ctx = {
        **spot_flow,
        "stablecoin_change_pct": _safe_ctx("稳定币水位", ctx.stablecoin_change_pct),
        "category_name": cat_name,
        "category_change_24h": cat_chg,
    }
    fl_score, fl_detail = score_flow(flow_ctx)

    # ④c 新闻事件情绪维（硬否决已在排雷层处理，这里只表达温和倾向）
    try:
        from crypto_intel_engine.news import aggregate_sentiment
        news_ctx = aggregate_sentiment(symbol)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 新闻情绪聚合失败 {symbol}: {e}")
        news_ctx = None
    sn_score, sn_detail = score_sentiment(news_ctx)

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
    dims = {"technical": tech_score, "derivatives": dv_score, "regime": rg_score,
            "flow": fl_score, "sentiment": sn_score}
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
            "flow": {"score": fl_score, "detail": fl_detail},
            "sentiment": {"score": sn_score, "detail": sn_detail},
        },
        "screen": {"score": screen.get("score"), "verdict": screen.get("verdict"),
                   "flags": screen.get("flags", []),
                   "unlock": screen.get("unlock"),
                   "hard_events": screen.get("hard_events", []),
                   # False = 否决闸没跑（≠ 查过了没问题），卡片/reasons 必须区分
                   "hard_events_checked": screen.get("hard_events_checked", True)},
        "data_quality": asdict(quality) if quality is not None else None,
        "derivatives_snapshot": deriv,
        "btc_regime": reg,
        "market_context": mctx,
        "reasons": _build_reasons(
            {"technical": {"detail": tech_detail}, "derivatives": {"detail": dv_detail},
             "flow": {"detail": fl_detail}, "sentiment": {"detail": sn_detail}},
            screen, rg_detail),
        "scorer_version": CRYPTO_SCORER_VERSION,
    })
    return result
