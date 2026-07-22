"""排雷层打分器 —— 加密独有的「这币该不该碰」体检（免费源，v1 做薄）。

对应 Jason 的判断「估值在加密里的用途是排雷不是选股」：不算内在价值，只用可量化的
免费信号识别明显的坑——供应稀释、大量未流通代币（解锁砸盘）、开发停滞、小市值。

两个入口：
- `market_context()`：市场级情绪/大势（恐慌贪婪 + BTC 主导率 + 总市值）。无需 symbol。
- `screen_coin(symbol)`：单币排雷体检 → 排雷分(0-100，越高越安全) + 逐维 + 红旗 + 结论。

排雷分是**透明扣分制**，不是黑盒。主流币（BTC/ETH）天然高分。

## v1 做薄的边界（诚实标注）
- 免费深度链上（MVRV/SOPR/活跃地址/巨鲸）未接（Glassnode/Nansen 付费）。
- 代币解锁精确时间表（DefiLlama emissions）未接——用 FDV/市值比做**解锁悬顶代理**
  （比值远大于 1 = 大量代币未流通 = 潜在稀释）。主流币无解锁，够用；碰山寨要深挖再接。
- 定性（团队/叙事/白皮书）走现有 `knowledge_engine` RAG，不在本打分器。
"""
import os
from typing import Any

from loguru import logger

# 恐慌贪婪分档（供 MoneyBill 直接引用语义）
_FNG_BANDS = [
    (0, 25, "极度恐惧（历史上常是波段买点）"),
    (25, 45, "恐惧"),
    (45, 55, "中性"),
    (55, 75, "贪婪"),
    (75, 101, "极度贪婪（过热，波段注意减仓）"),
]


def _fng_label(value: int) -> str:
    for lo, hi, label in _FNG_BANDS:
        if lo <= value < hi:
            return label
    return "未知"


def market_context() -> dict[str, Any]:
    """市场级大势/情绪快照：恐慌贪婪 + BTC 主导率 + 总市值。单项失败不拖垮整体。"""
    from acquisition.markets.crypto_intel import coingecko_global, fear_greed_index

    ctx: dict[str, Any] = {}
    try:
        fng = fear_greed_index(limit=1)
        v = fng[0]["value"] if fng else None
        ctx["fear_greed"] = {"value": v, "label": _fng_label(v)} if v is not None else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"恐慌贪婪抓取失败: {e}")
        ctx["fear_greed"] = None
    try:
        g = coingecko_global()
        ctx["btc_dominance"] = round((g.get("market_cap_percentage") or {}).get("btc", 0), 2)
        ctx["total_market_cap_usd"] = (g.get("total_market_cap") or {}).get("usd")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"CoinGecko global 抓取失败: {e}")
        ctx["btc_dominance"] = None
        ctx["total_market_cap_usd"] = None
    return ctx


def screen_coin(symbol: str, *, extras: dict | None = None,
                check_news: bool = True) -> dict[str, Any]:
    """单币排雷体检。返回 {symbol, coingecko_id, ambiguous, score, verdict, flags, dimensions}。

    score 0-100 越高越安全；verdict = pass(≥75) / caution(50-74) / avoid(<50) / unknown。
    查不到该币（解析失败）时 verdict='unknown'、score=None。

    Args:
        extras: 调用方已取到的深化项（`tvl_change_pct` / `thin_liquidity` / 也可直接给
            `unlock_pct_30d` 省一次出网）。
        check_news: 是否跑事件硬否决（下架/被盗/监管 → 直接判 avoid）。批量筛币时可关掉省请求。
    """
    from acquisition.markets.crypto_intel import coingecko_coin
    from crypto_intel_engine.resolver import base_asset, resolve_coingecko_id

    base = base_asset(symbol)
    coin_id, ambiguous = resolve_coingecko_id(symbol)
    result: dict[str, Any] = {
        "symbol": symbol, "base_asset": base, "coingecko_id": coin_id,
        "ambiguous": ambiguous, "score": None, "verdict": "unknown",
        "flags": [], "dimensions": {},
    }
    if not coin_id:
        result["flags"].append(f"无法解析 {base} 到 CoinGecko（排雷数据缺失）")
        return result

    try:
        coin = coingecko_coin(coin_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"排雷取数失败 {symbol}({coin_id}): {e}")
        result["flags"].append("排雷取数失败（网络/限速）")
        return result

    md = coin.get("market_data", {}) or {}
    dev = coin.get("developer_data", {}) or {}

    # 真解锁日程（DefiLlama 免费静态数据集）。拿不到就退回 FDV 代理，由 score_dimensions 判。
    merged_extras = dict(extras or {})
    if "unlock_pct_30d" not in merged_extras:
        try:
            from acquisition.markets.crypto_onchain import get_unlock_summary
            summary = get_unlock_summary(coin_id, md.get("circulating_supply"))
            if summary:
                merged_extras["unlock_pct_30d"] = summary.get("pct_of_supply")
                result["unlock"] = {"upcoming_tokens": summary.get("upcoming_tokens"),
                                    "pct_of_supply": summary.get("pct_of_supply"),
                                    "next_events": summary.get("events", [])[:5]}
        except Exception as e:  # noqa: BLE001 — 解锁取不到就用代理，不阻断体检
            logger.debug(f"解锁数据取数失败 {symbol}: {e}")

    dims, score, flags = score_dimensions(md, dev, merged_extras)
    result["dimensions"] = dims
    result["categories"] = coin.get("categories") or []   # 供资金流维查叙事板块轮动
    result["score"] = score
    result["verdict"] = verdict_of(score)

    # ⛔ 事件硬否决：下架/被盗/监管执法 —— 不打分、直接判 avoid（不给任何维度稀释的机会）
    #
    # 这道闸 fail-open（取不到公告就放行）是刻意的——事件源挂了不该阻断整张体检卡。但**必须
    # 留痕**：`hard_events_checked=False` + 一条 flag，否则「查过了没问题」和「压根没查」
    # 在卡片上长得一模一样，一个昨天刚被宣布下架的币会被照常推荐买入而 Jason 毫不知情。
    if check_news:
        try:
            from crypto_intel_engine.news import check_hard_events
            ev = check_hard_events(symbol)
            result["hard_events_checked"] = bool(ev.get("checked"))
            if ev.get("veto"):
                result["verdict"] = "avoid"
                result["hard_events"] = ev["events"]
                flags = [f"⛔ {r}" for r in ev["reasons"]] + list(flags)
            elif not ev.get("checked"):
                flags = ["⚠️ 事件否决检查未执行（币安公告源不可用），下架/被盗类风险未排查",
                         *flags]
        except Exception as e:  # noqa: BLE001 — 事件源挂了不该阻断体检
            logger.warning(f"事件否决检查失败 {symbol}: {e}")
            result["hard_events_checked"] = False
            flags = ["⚠️ 事件否决检查未执行（币安公告源不可用），下架/被盗类风险未排查", *flags]

    if ambiguous:
        flags = [*flags, f"⚠️ {base} 由 symbol 回退匹配，可能撞名，请核对 coingecko_id"]
    result["flags"] = flags
    return result


def verdict_of(score: int | None) -> str:
    """排雷分 → 结论。None=unknown / ≥75 pass / 50-74 caution / <50 avoid。"""
    if score is None:
        return "unknown"
    return "pass" if score >= 75 else "caution" if score >= 50 else "avoid"


# ════════════════════ 币版驾驶舱：融合打分（择时三维 + 排雷否决闸）════════════════════
#
# 对标股票 `cockpit_engine.scorer.score_cockpit`，但维度换成加密两条腿的择时层：
# 技术择时 / 衍生品情绪 / 大势闸门。排雷层（screen_coin）**当否决闸**，不当加权维——
# 择时再好，排雷判 avoid（供应无上限/解锁悬顶/开发停滞）就强制压到 HOLD 以下，不给买。
#
# 阈值/仓位/硬钳口径与股票一致（复用 cockpit_engine.scorer 的纯函数，那模块零依赖）。
CRYPTO_SCORER_VERSION = "rule:crypto-cockpit-v2"

# v1（三维）：留作回滚锚点，`.env CRYPTO_SCORER=v1` 即可退回旧口径。
CRYPTO_WEIGHTS_V1 = {"technical": 0.45, "regime": 0.30, "derivatives": 0.25}

# v2（五维）：技术仍是主心骨但让出权重给两条加密独有的新腿——
# flow（资金流：稳定币水位/现货主动买盘/叙事轮动）与 sentiment（新闻事件）。
CRYPTO_WEIGHTS_V2 = {"technical": 0.30, "derivatives": 0.20, "regime": 0.20,
                     "flow": 0.15, "sentiment": 0.15}

CRYPTO_WEIGHTS = CRYPTO_WEIGHTS_V2      # 默认口径（下游只读这个名字）


def active_weights() -> dict[str, float]:
    """当前生效权重。`.env CRYPTO_SCORER=v1` 回滚到三维旧口径（出事一键退）。"""
    return CRYPTO_WEIGHTS_V1 if os.getenv("CRYPTO_SCORER", "v2").lower() == "v1" else CRYPTO_WEIGHTS_V2


# ── 历史分位内核 ──
# 「资金费率 0.01% 算高吗」这种问题，写死阈值永远答不准（不同币、不同周期基准天差地别）。
# 有了 `crypto_metrics` 的历史序列就能直接问：**它在自己过去 N 天里排第几**。
_PCTILE_MIN_SAMPLES = 30


def pctile_score(history: list[float] | None, current: float | None, *,
                 invert: bool = False, min_samples: int = _PCTILE_MIN_SAMPLES) -> float | None:
    """当前值在历史中的分位 → 0-100 分。样本不足 `min_samples` 返 None（不硬凑）。

    Args:
        invert: True 表示「越高越危险」（如资金费率：高分位=多头拥挤=偏空）。

    Returns:
        分位分。invert=False 时高分位→高分；invert=True 时高分位→低分。
    """
    if current is None or not history or len(history) < min_samples:
        return None
    below = sum(1 for h in history if h < current)
    pct = below / len(history)
    return round((1.0 - pct) * 100 if invert else pct * 100, 1)

# 排雷 avoid 时 composite 的封顶（44 < HOLD 线 45 → 落进 SELL/回避区，绝不给买入级结论）
SCREEN_VETO_CAP = 44.0
# 排雷 caution 时的温和扣分（不否决，但降级）
SCREEN_CAUTION_PENALTY = 8.0


# 衍生品各子信号权重（和不必为 1，按可得项重归一）
_DERIV_WEIGHTS = {
    "funding": 0.30,        # 资金费率：最经典的拥挤度表
    "oi_price": 0.25,       # 未平仓×价格背离：区分真突破与逼空
    "taker": 0.20,          # 主动吃单方向：谁在动手
    "top_trader": 0.15,     # 大户持仓比：顺向的聪明钱
    "long_short": 0.05,     # 散户账户比：反向指标，权重最低（噪声大）
    "basis": 0.05,          # 期现基差：溢价本身
}


def _score_oi_price(oi_history: list[dict] | None,
                    price_change_pct: float | None) -> tuple[float | None, dict]:
    """未平仓变化 × 价格变化 → 四象限打分（衍生品维里信息量最高的一条）。

    杠杆钱进场还是离场，配上价格方向才有意义：
      OI↑ 价↑ = 新钱做多，趋势有燃料（最强）    OI↑ 价↓ = 空头堆积/杠杆做空（最弱）
      OI↓ 价↑ = 空头回补撑起来的涨（虚）        OI↓ 价↓ = 多头去杠杆出清（跌得健康）

    ⚠️ 调用方必须保证 `price_change_pct` 与 `oi_history` **跨度一致**（都从序列首端到今天）。
    窗口错配会把币判进相反象限，detail 里回吐 `window_days` 供核对。
    """
    if not oi_history or len(oi_history) < 2 or price_change_pct is None:
        return None, {}
    try:
        old = float(oi_history[0].get("oi") or 0)
        new = float(oi_history[-1].get("oi") or 0)
    except (TypeError, ValueError):
        return None, {}
    if old <= 0:
        return None, {}

    oi_chg = (new - old) / old * 100
    from common.scoring_utils import clamp
    oi_up, price_up = oi_chg > 0, price_change_pct > 0
    if oi_up and price_up:
        base, label = 70.0, "新钱做多（OI↑价↑，趋势有燃料）"
    elif oi_up and not price_up:
        base, label = 30.0, "空头堆积（OI↑价↓，杠杆在做空）"
    elif not oi_up and price_up:
        base, label = 45.0, "空头回补（OI↓价↑，涨势缺新钱）"
    else:
        base, label = 55.0, "多头去杠杆（OI↓价↓，出清中）"
    # 变化幅度越大信号越强，最多再推 ±10 分
    strength = clamp(abs(oi_chg) / 2.0, 0, 10) * (1 if base >= 50 else -1)
    return round(clamp(base + strength, 0, 100), 1), {
        "oi_change_pct": round(oi_chg, 2), "price_change_pct": price_change_pct,
        "window_days": len(oi_history), "label": label}


def score_derivatives(snap: dict | None, *, funding_history: list[float] | None = None,
                      price_change_pct: float | None = None) -> tuple[float | None, dict]:
    """币安衍生品情绪 → 0-100（50 中性）。六个子信号加权，缺项自动重归一。

    - **资金费率**：有历史就用**分位**（它在自己过去 60 天里排第几），没历史回落到固定阈值
      并标 `funding_basis='threshold'`（诚实标注，别假装有分位）。高费率=多头拥挤=偏空。
    - **OI×价格**：四象限，见 `_score_oi_price`。
    - **主动吃单比 / 大户持仓比**：顺向。**散户账户比**：反向、权重最低。
    - **期现基差**：正溢价=偏热，轻微反向。

    全空返回 (None, {"available": False})。
    """
    if not snap:
        return None, {"available": False}
    from common.scoring_utils import clamp
    parts: dict[str, float] = {}
    detail: dict[str, Any] = {"available": True}

    # ① 资金费率：优先历史分位，回落固定阈值
    fr = (snap.get("funding") or {}).get("funding_rate")
    if isinstance(fr, (int, float)):
        pct = pctile_score(funding_history, fr, invert=True)
        if pct is not None:
            parts["funding"] = pct
            detail["funding_basis"] = "percentile"
            detail["funding_pctile"] = pct
            detail["funding_samples"] = len(funding_history or [])
        else:
            # 0.01%(0.0001) 中性附近；±0.1%(0.001) 视为过热/恐慌极值 → ±20 分
            parts["funding"] = 50.0 - clamp(fr * 20000.0, -20.0, 20.0)
            detail["funding_basis"] = "threshold"
            detail["degraded"] = True      # 历史不足，本项是退化口径
        detail["funding_rate"] = fr
        detail["funding_score"] = round(parts["funding"], 1)

    # ② 未平仓 × 价格背离
    oi_score, oi_detail = _score_oi_price(snap.get("oi_history"), price_change_pct)
    if oi_score is not None:
        parts["oi_price"] = oi_score
        detail["oi_price"] = {**oi_detail, "score": oi_score}
    oi = snap.get("open_interest") or {}
    if oi:
        detail["open_interest"] = oi.get("oi")

    # ③ 主动吃单方向（顺向）
    tf_ratio = (snap.get("taker_flow") or {}).get("ratio")
    if isinstance(tf_ratio, (int, float)) and tf_ratio > 0:
        parts["taker"] = clamp(50.0 + clamp((tf_ratio - 1.0) * 60.0, -20.0, 20.0), 0, 100)
        detail["taker_ratio"] = tf_ratio
        detail["taker_score"] = round(parts["taker"], 1)

    # ④ 大户持仓比（顺向的聪明钱）
    tt_ratio = (snap.get("top_trader") or {}).get("ratio")
    if isinstance(tt_ratio, (int, float)) and tt_ratio > 0:
        parts["top_trader"] = clamp(50.0 + clamp((tt_ratio - 1.0) * 20.0, -15.0, 15.0), 0, 100)
        detail["top_trader_ratio"] = tt_ratio
        detail["top_trader_score"] = round(parts["top_trader"], 1)

    # ⑤ 散户账户比（反向指标，权重最低）
    ratio = (snap.get("long_short") or {}).get("ratio")
    if isinstance(ratio, (int, float)) and ratio > 0:
        parts["long_short"] = clamp(50.0 - clamp((ratio - 1.0) * 20.0, -15.0, 15.0), 0, 100)
        detail["long_short_ratio"] = ratio
        detail["long_short_score"] = round(parts["long_short"], 1)

    # ⑥ 期现基差（溢价=偏热，轻微反向）
    basis_rate = (snap.get("basis") or {}).get("basis_rate")
    if isinstance(basis_rate, (int, float)):
        parts["basis"] = clamp(50.0 - clamp(basis_rate * 2000.0, -10.0, 10.0), 0, 100)
        detail["basis_rate"] = basis_rate
        detail["basis_score"] = round(parts["basis"], 1)

    if not parts:
        return None, {"available": False}

    total_w = sum(_DERIV_WEIGHTS[k] for k in parts)
    score = sum(parts[k] * _DERIV_WEIGHTS[k] for k in parts) / total_w
    detail["components_used"] = sorted(parts)
    return round(clamp(score, 0, 100), 1), detail


# 资金流各子信号权重
_FLOW_WEIGHTS = {
    "stablecoin": 0.40,     # 稳定币水位：整个加密世界的进水口
    "spot_taker": 0.35,     # 现货主动买入占比：这个币自己的真实买压
    "category": 0.25,       # 叙事轮动：所属赛道在被买还是被抛
}

# 24h 成交额低于此值视为流动性太薄（滑点不可控，标风险旗但不打分）
THIN_LIQUIDITY_USDT = 5_000_000.0


def score_flow(ctx: dict | None) -> tuple[float | None, dict]:
    """资金流 → 0-100（50 中性）。加密独有的「钱在往哪走」维度。

    ⚠️ **BTC 现货 ETF 净流入本该是这一维的主力，但 2026-07-21 实测无零 key 免费源**
    （DefiLlama 无端点、Farside 被 Cloudflare 挡）。替代方案是币安现货 K 线自带的
    `taker_buy_ratio`（主动买入占比）——它是这个币自己的真实买压，粒度比 ETF 更细、
    还自带历史可回测。缺 ETF 这一项是**如实的能力边界**，不拿劣质代理硬凑。

    Args:
        ctx: {stablecoin_change_pct, spot_taker_buy_ratio, category_change_24h,
              quote_volume_24h}
    """
    if not ctx:
        return None, {"available": False}
    from common.scoring_utils import clamp
    parts: dict[str, float] = {}
    detail: dict[str, Any] = {"available": True}

    # ① 稳定币总供应变化（30 日 %）：扩张=新钱进场
    sc = ctx.get("stablecoin_change_pct")
    if isinstance(sc, (int, float)):
        parts["stablecoin"] = clamp(50.0 + clamp(sc * 10.0, -25.0, 25.0), 0, 100)
        detail["stablecoin_change_pct"] = round(sc, 3)
        detail["stablecoin_score"] = round(parts["stablecoin"], 1)

    # ② 现货主动买入占比：0.5 势均力敌，0.55 已是明显买方主动
    tb = ctx.get("spot_taker_buy_ratio")
    if isinstance(tb, (int, float)) and 0 < tb < 1:
        parts["spot_taker"] = clamp(50.0 + clamp((tb - 0.5) * 400.0, -20.0, 20.0), 0, 100)
        detail["spot_taker_buy_ratio"] = round(tb, 4)
        detail["spot_taker_score"] = round(parts["spot_taker"], 1)

    # ③ 所属赛道 24h 市值变化
    cat = ctx.get("category_change_24h")
    if isinstance(cat, (int, float)):
        parts["category"] = clamp(50.0 + clamp(cat * 2.0, -15.0, 15.0), 0, 100)
        detail["category_change_24h"] = round(cat, 2)
        detail["category_name"] = ctx.get("category_name")
        detail["category_score"] = round(parts["category"], 1)

    # 流动性：不是多空信号，是风险旗（薄盘滑点不可控）
    qv = ctx.get("quote_volume_24h")
    if isinstance(qv, (int, float)):
        detail["quote_volume_24h"] = round(qv, 2)
        detail["thin_liquidity"] = qv < THIN_LIQUIDITY_USDT

    if not parts:
        return None, {"available": False}
    total_w = sum(_FLOW_WEIGHTS[k] for k in parts)
    score = sum(parts[k] * _FLOW_WEIGHTS[k] for k in parts) / total_w
    detail["components_used"] = sorted(parts)
    return round(clamp(score, 0, 100), 1), detail


# 新闻情绪至少要这么多篇才给分（一两篇标题的情绪是噪声，不是信号）
NEWS_MIN_ARTICLES = 3


def score_sentiment(ctx: dict | None) -> tuple[float | None, dict]:
    """新闻事件情绪 → 0-100（50 中性）。样本不足 `NEWS_MIN_ARTICLES` 返 None。

    ⛔ **硬否决事件（下架/被盗/监管执法）不在这里打分**——它们走排雷否决闸
    （`screen.verdict='avoid'`），因为「利空到什么程度」不该被其它维度的高分稀释掉。
    这一维只表达温和的情绪倾向。

    Args:
        ctx: {net_sentiment: -1..1, article_count: int, positive/negative/neutral: int}
    """
    if not ctx:
        return None, {"available": False}
    n = ctx.get("article_count") or 0
    net = ctx.get("net_sentiment")
    if n < NEWS_MIN_ARTICLES or not isinstance(net, (int, float)):
        return None, {"available": False, "article_count": n,
                      "reason": f"新闻样本不足（{n} < {NEWS_MIN_ARTICLES}）"}
    from common.scoring_utils import clamp
    score = clamp(50.0 + net * 40.0, 0, 100)
    return round(score, 1), {
        "available": True,
        "net_sentiment": round(net, 3),
        "article_count": n,
        "positive": ctx.get("positive"),
        "negative": ctx.get("negative"),
        "neutral": ctx.get("neutral"),
    }


# 大势微调项（恐慌贪婪 + 主导率 + 宏观）加总后的绝对上限。取 12 是为了让熊市基准 30
# 顶多到 42、牛市基准 65 顶多到 77 —— 都留在各自那一侧，微调永远翻不了大势的盘。
_REGIME_ADJ_CAP = 12.0


def score_regime(btc_regime: dict | None, market_ctx: dict | None, *,
                 dominance_change_pct: float | None = None,
                 macro: dict | None = None,
                 is_btc: bool = False) -> tuple[float | None, dict]:
    """大势闸门 → 0-100。BTC 200 日牛/熊定基调，恐慌贪婪微调（极端恐惧=机会/极端贪婪=风险）。

    90% 山寨是 BTC 影子：BTC 熊市显著压低做多分，是波段最重要的一道闸。

    Args:
        dominance_change_pct: BTC 主导率近 30 天变化（百分点）。**主导率升 = 钱在往 BTC
            集中 = 山寨失血**，所以对山寨是利空、对 BTC 自己是利好 —— 靠 `is_btc` 区分。
            （此前项目把主导率取回来了却从没进过打分，这次接上。）
        macro: {dxy_change_pct, nasdaq_change_pct}，美元走强=risk-off 压制加密。

    ⛔ 所有微调项**加总后再统一封顶 ±`_REGIME_ADJ_CAP`**。分项各自 clamp 是不够的：
    恐慌贪婪(±8) + 主导率(±8) + 宏观(±11) 可以叠到 +27，把熊市基准 30 抬到 57 —— 越过
    50 中轴，这道「BTC 熊市压低做多分」的闸就名存实亡了。封到 ±12 后熊市上限 42、
    牛市上限 77：微调仍有意义，但永远翻不了大势的盘。
    """
    from common.scoring_utils import clamp
    reg = (btc_regime or {}).get("regime")
    if reg == "bull":
        base = 65.0
    elif reg == "bear":
        base = 30.0
    else:
        base = 50.0
    detail: dict[str, Any] = {"available": reg is not None, "btc_regime": reg,
                              "pct_from_ma": (btc_regime or {}).get("pct_from_ma")}

    fng = (market_ctx or {}).get("fear_greed") or {}
    v = fng.get("value")
    adj = 0.0
    if isinstance(v, (int, float)):
        if v < 25:
            adj = 8.0            # 极度恐惧：波段常是买点
        elif v >= 75:
            adj = -8.0           # 极度贪婪：过热风险
        else:
            adj = (45.0 - v) / 45.0 * 5.0   # 中间区温和线性
        detail["fear_greed"] = v
    detail["btc_dominance"] = (market_ctx or {}).get("btc_dominance")

    # BTC 主导率趋势：对山寨反向、对 BTC 顺向
    if isinstance(dominance_change_pct, (int, float)):
        dom_adj = clamp(dominance_change_pct * 2.0, -8.0, 8.0)
        adj += dom_adj if is_btc else -dom_adj
        detail["dominance_change_pct"] = round(dominance_change_pct, 2)
        detail["dominance_adj"] = round(dom_adj if is_btc else -dom_adj, 2)

    # 宏观：美元走强压制风险资产；纳指是风险偏好的同向表
    if macro:
        macro_adj = 0.0
        dxy = macro.get("dxy_change_pct")
        if isinstance(dxy, (int, float)):
            macro_adj += clamp(-dxy * 1.5, -6.0, 6.0)
            detail["dxy_change_pct"] = round(dxy, 2)
        ndx = macro.get("nasdaq_change_pct")
        if isinstance(ndx, (int, float)):
            macro_adj += clamp(ndx * 0.5, -5.0, 5.0)
            detail["nasdaq_change_pct"] = round(ndx, 2)
        if macro_adj:
            adj += macro_adj
            detail["macro_adj"] = round(macro_adj, 2)

    if reg is None and not isinstance(v, (int, float)):
        return None, {"available": False}

    # 微调项加总封顶：谁也别想靠堆叠利多把熊市基准顶过 50 中轴（见 docstring）
    adj = clamp(adj, -_REGIME_ADJ_CAP, _REGIME_ADJ_CAP)
    detail["total_adj"] = round(adj, 2)
    return round(clamp(base + adj, 0, 100), 1), detail


def score_crypto_cockpit(dimensions: dict[str, float | None],
                         screen_result: dict | None = None,
                         dynamic_levels: dict | None = None,
                         current_position_pct: float = 0.0,
                         max_position_pct: float = 0.5,
                         quality: Any = None,
                         calibration_factor: float = 1.0) -> dict:
    """择时五维 + 排雷否决闸 → 综合分/动作/价位/目标仓位。镜像 `score_cockpit`。

    dimensions: {technical, derivatives, regime, flow, sentiment} → 0-100 或 None
        （缺维自动重归一，所以新维度取不到数只降 coverage、不拖垮打分）。
    screen_result: `screen_coin` 产出；verdict=='avoid' → composite 硬封顶到 SCREEN_VETO_CAP
        （否决闸）；'caution' → 温和扣分。
    quality/calibration_factor: 同 cockpit（数据质量硬钳 + 历史命中率校准，作用在硬钳前）。
    """
    from cockpit_engine.scorer import (
        CLAMP_CAP,
        _recommendation,
        _target_position_pct,
    )
    from common.scoring_utils import clamp

    weights = active_weights()
    # 只认当前权重表里的维度：v1 回滚时多出来的 flow/sentiment 自动被忽略，不 KeyError
    available = {k: v for k, v in dimensions.items() if v is not None and k in weights}
    if not available:
        return {
            "composite": None, "recommendation": "N/A",
            "suggested_position_pct": 0.0, "suggested_add_pct": 0.0,
            "current_position_pct": round(current_position_pct, 1),
            "stop_loss": None, "take_profit": None, "weights_used": {},
            "available_dimensions": [], "dimension_coverage": 0.0,
            "raw_composite": None, "calibration_factor": 1.0, "adjustments": (),
        }

    total_w = sum(weights[k] for k in available)
    weights_used = {k: round(weights[k] / total_w, 4) for k in available}
    composite = round(sum(available[k] * weights_used[k] for k in available), 1)
    dimension_coverage = round(total_w / sum(weights.values()), 4)
    raw_composite = composite
    adjustments: list[str] = []

    # 历史命中率校准（硬钳之前，只下调）
    if calibration_factor is not None and calibration_factor != 1.0:
        composite = round(composite * calibration_factor, 1)
        adjustments.append("confidence_calibrated_by_history")

    # 数据质量硬钳（核心数据降级 → 封顶 CLAMP_CAP）
    if quality is not None and getattr(quality, "core_degraded", False) and composite > CLAMP_CAP:
        composite = CLAMP_CAP
        adjustments.append("composite_capped_core_data_degraded")

    # ── 排雷否决闸（加密独有）──
    verdict = (screen_result or {}).get("verdict")
    if verdict == "avoid" and composite > SCREEN_VETO_CAP:
        composite = SCREEN_VETO_CAP
        adjustments.append("capped_by_screen_veto")
    elif verdict == "caution":
        composite = round(max(0.0, composite - SCREEN_CAUTION_PENALTY), 1)
        adjustments.append("screen_caution_penalty")

    composite = round(clamp(composite, 0, 100), 1)

    stop_loss = take_profit = None
    if isinstance(dynamic_levels, dict):
        stop_loss = dynamic_levels.get("atr_stop_loss") or dynamic_levels.get("trailing_stop")
        for lv in (dynamic_levels.get("take_profit_levels") or []):
            if isinstance(lv, dict) and lv.get("level") == 2:
                take_profit = lv.get("price")
                break

    target_pct = _target_position_pct(composite, max_position_pct)
    add_pct = round(max(0.0, target_pct - current_position_pct), 1) if target_pct > 0 else 0.0

    return {
        "composite": composite,
        "recommendation": _recommendation(composite),
        "suggested_position_pct": target_pct,
        "suggested_add_pct": add_pct,
        "current_position_pct": round(current_position_pct, 1),
        "stop_loss": stop_loss, "take_profit": take_profit,
        "weights_used": weights_used,
        "available_dimensions": list(available.keys()),
        "dimension_coverage": dimension_coverage,
        "raw_composite": raw_composite,
        "calibration_factor": calibration_factor,
        "adjustments": tuple(adjustments),
    }


def score_dimensions(md: dict, dev: dict, extras: dict | None = None) -> tuple[dict, int, list[str]]:
    """纯扣分逻辑（不出网，可离线测）：吃 CoinGecko market_data/developer_data 两个 dict，
    产 (dimensions, score 0-100, flags)。透明扣分制，主流币天然高分。

    Args:
        extras: 可选深化项 —— `{unlock_pct_30d, tvl_change_pct, thin_liquidity}`。
            `unlock_pct_30d` 是 DefiLlama 的**真解锁日程**。它与 FDV/市值比那个代理
            **取更重的一个**扣分（不叠加——同一件事不罚两遍）。

            ⛔ 不能「有日程就完全弃用代理」：日程只看未来 30 天，一个 FDV/市值=6（仅 17%
            流通）、悬崖落在第 35 天的币会拿到 `pct_of_supply=0`，于是 -25 的稀释罚分整个
            消失、排雷分凭空跳 25 点、verdict 从 avoid 翻成 pass，`SCREEN_VETO_CAP` 封顶
            随之失效。取 max 既保住「不重复罚」，又不让 30 天窗口内的清白抹掉结构性稀释风险。
    """
    circ = md.get("circulating_supply")
    max_sup = md.get("max_supply")
    mcap = (md.get("market_cap") or {}).get("usd")
    fdv = (md.get("fully_diluted_valuation") or {}).get("usd")
    commits_4w = dev.get("commit_count_4_weeks")

    dims = {
        "circulating_supply": circ, "max_supply": max_sup,
        "market_cap_usd": mcap, "fdv_usd": fdv,
        "fdv_mcap_ratio": round(fdv / mcap, 2) if (fdv and mcap) else None,
        "commits_4w": commits_4w, "stars": dev.get("stars"),
    }

    score = 100
    flags: list[str] = []

    # ① 供应上限（无上限 = 潜在通胀稀释）
    if max_sup is None:
        score -= 12
        flags.append("无供应上限（潜在通胀稀释）")

    # ② 解锁压力：真日程（未来30天）与 FDV/市值代理（结构性稀释）**取更重的一个**扣分
    ex = extras or {}
    unlock_pct = ex.get("unlock_pct_30d")
    dims["unlock_pct_30d"] = unlock_pct

    sched_pen, sched_flag = 0, None
    if isinstance(unlock_pct, (int, float)):
        if unlock_pct >= 5:
            sched_pen, sched_flag = 28, f"未来30天解锁 {unlock_pct}% 流通量（重度抛压，砸盘风险高）"
        elif unlock_pct >= 3:
            sched_pen, sched_flag = 18, f"未来30天解锁 {unlock_pct}% 流通量（明显抛压）"
        elif unlock_pct >= 1:
            sched_pen, sched_flag = 8, f"未来30天解锁 {unlock_pct}% 流通量（轻度抛压）"

    fdv_pen, fdv_flag = 0, None
    ratio = dims["fdv_mcap_ratio"]
    if ratio is not None:
        if ratio >= 3:
            fdv_pen, fdv_flag = 25, f"大量代币未流通（FDV/市值={ratio}，解锁砸盘风险高）"
        elif ratio >= 1.5:
            fdv_pen, fdv_flag = 12, f"部分代币未流通（FDV/市值={ratio}，留意解锁）"

    if sched_pen >= fdv_pen:
        dims["unlock_basis"] = "schedule" if isinstance(unlock_pct, (int, float)) else "none"
        score -= sched_pen
        if sched_flag:
            flags.append(sched_flag)
    else:
        dims["unlock_basis"] = "fdv_proxy"
        score -= fdv_pen
        if fdv_flag:
            flags.append(fdv_flag)

    # ③ 开发活跃度（4 周零提交 = 项目可能已死）
    if commits_4w is None:
        score -= 5
        flags.append("开发活跃度未知")
    elif commits_4w == 0:
        score -= 30
        flags.append("开发停滞（GitHub 近 4 周零提交）")
    elif commits_4w < 5:
        score -= 10
        flags.append(f"开发冷清（近 4 周仅 {commits_4w} 次提交）")

    # ④ 市值（小市值 = 流动性差/易被操纵）
    if mcap is not None:
        if mcap < 100_000_000:
            score -= 20
            flags.append("小市值（<1亿美元，流动性/操纵风险）")
        elif mcap < 1_000_000_000:
            score -= 8
            flags.append("中小市值（<10亿美元，波动更大）")

    # ⑤ 链上真实使用度：TVL 在缩 = 用的人在跑（只对有 TVL 的链/协议生效）
    tvl_chg = ex.get("tvl_change_pct")
    dims["tvl_change_pct"] = tvl_chg
    if isinstance(tvl_chg, (int, float)):
        if tvl_chg <= -30:
            score -= 15
            flags.append(f"链上资金大幅流出（TVL 近30天 {tvl_chg:+.1f}%）")
        elif tvl_chg <= -15:
            score -= 7
            flags.append(f"链上资金流出（TVL 近30天 {tvl_chg:+.1f}%）")

    # ⑥ 盘口太薄：滑点不可控，波段进出容易被吃掉利润
    if ex.get("thin_liquidity"):
        score -= 10
        flags.append("24h 成交额过低（盘口薄，滑点不可控）")

    return dims, max(0, min(100, score)), flags
