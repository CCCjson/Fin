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


def screen_coin(symbol: str) -> dict[str, Any]:
    """单币排雷体检。返回 {symbol, coingecko_id, ambiguous, score, verdict, flags, dimensions}。

    score 0-100 越高越安全；verdict = pass(≥75) / caution(50-74) / avoid(<50) / unknown。
    查不到该币（解析失败）时 verdict='unknown'、score=None。
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
    dims, score, flags = score_dimensions(md, dev)
    result["dimensions"] = dims
    result["score"] = score
    result["verdict"] = verdict_of(score)
    if ambiguous:
        flags = [*flags, f"⚠️ {base} 由 symbol 回退匹配，可能撞名，请核对 coingecko_id"]
    result["flags"] = flags
    return result


def verdict_of(score: int | None) -> str:
    """排雷分 → 结论。None=unknown / ≥75 pass / 50-74 caution / <50 avoid。"""
    if score is None:
        return "unknown"
    return "pass" if score >= 75 else "caution" if score >= 50 else "avoid"


def score_dimensions(md: dict, dev: dict) -> tuple[dict, int, list[str]]:
    """纯扣分逻辑（不出网，可离线测）：吃 CoinGecko market_data/developer_data 两个 dict，
    产 (dimensions, score 0-100, flags)。透明扣分制，主流币天然高分。"""
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

    # ② 解锁悬顶代理：FDV/市值比（远大于 1 = 大量代币未流通，未来解锁砸盘风险）
    ratio = dims["fdv_mcap_ratio"]
    if ratio is not None:
        if ratio >= 3:
            score -= 25
            flags.append(f"大量代币未流通（FDV/市值={ratio}，解锁砸盘风险高）")
        elif ratio >= 1.5:
            score -= 12
            flags.append(f"部分代币未流通（FDV/市值={ratio}，留意解锁）")

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

    return dims, max(0, min(100, score)), flags
