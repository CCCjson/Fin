"""市场级上下文装配 —— 稳定币水位 / 叙事板块 / 宏观 / BTC 主导率趋势。

## 为什么单独一层 + TTL 缓存

这些量是**全市场共享**的（稳定币总供应跟你分析哪个币无关），但决策入口是**逐币**调用的
`analyze_crypto_symbol`。策略一个 tick 扫 10 个币，若每个币都去打一遍 DefiLlama/CoinGecko/
Yahoo，就是 30 次重复请求换同一个数字——既烧限速额度又拖慢 tick。

所以：市场级数据在这里取一次，进程内缓存 `_TTL` 秒，同一轮扫描全部复用。

## 取数优先级：库里有就读库，没有才出网

`crypto_updater` 每轮会把这些量落进 `crypto_metrics`。库里够算就直接算（零请求）；
只有冷启动/刚上线那几天库里没历史，才回落到实时拉取。
"""
import time
from typing import Any

from loguru import logger

# 市场级上下文缓存时长（秒）。这些量都是日频的，10 分钟内不会有意义变化。
_TTL = 600.0

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, producer):
    """带 TTL 的进程内缓存。producer 抛异常时返回 None 并记 warning（不缓存失败结果）。"""
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        value = producer()
    except Exception as e:  # noqa: BLE001 — 上下文取不到只降级，不阻断分析
        logger.warning(f"市场上下文 {key} 取数失败: {e}")
        return None
    _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    """清空缓存（测试用，或需要强制刷新时）。"""
    _cache.clear()


def _pct_change(series: list[float], periods: int) -> float | None:
    """序列末值相对 `periods` 期前的百分比变化。样本不足返 None。"""
    if not series or len(series) <= periods:
        return None
    old = series[-(periods + 1)]
    if not old:
        return None
    return round((series[-1] - old) / old * 100, 3)


# ──────────────────── 稳定币水位（资金流主力）────────────────────


def stablecoin_change_pct(days: int = 30) -> float | None:
    """稳定币总供应近 `days` 天变化 %。库里够算就读库，不够才拉 DefiLlama。"""
    def _produce():
        from crypto_intel_engine.store import read_market_series
        from data_engine.storage.database import get_session
        session = get_session()
        try:
            series = read_market_series(session, "stablecoin_supply", days=days + 10)
        finally:
            session.close()
        chg = _pct_change(series, days)
        if chg is not None:
            return chg
        # 冷启动：库里还没攒够历史，直接拉全序列自己算
        from acquisition.markets.crypto_onchain import stablecoin_supply
        rows = stablecoin_supply(limit_days=days + 10)
        return _pct_change([r["total_usd"] for r in rows], days)

    return _cached(f"stablecoin_{days}", _produce)


# ──────────────────── 叙事板块轮动 ────────────────────


def category_changes() -> dict[str, float]:
    """CoinGecko 板块 id → 24h 市值变化 %。取不到返空 dict。"""
    def _produce():
        from acquisition.markets.crypto_intel import coingecko_categories
        out: dict[str, float] = {}
        for row in coingecko_categories():
            cid, chg = row.get("id"), row.get("market_cap_change_24h")
            if cid and isinstance(chg, (int, float)):
                out[cid] = float(chg)
        return out

    return _cached("categories", _produce) or {}


def pick_category(coin_categories: list[str] | None) -> tuple[str | None, float | None]:
    """从币的板块列表里挑一个有行情数据的，返回 (板块名, 24h变化%)。

    CoinGecko 的 `/coins/{id}.categories` 给的是**板块显示名**（"Smart Contract Platform"），
    而 `/coins/categories` 的 key 是 slug（"smart-contract-platform"）→ 名字转 slug 再查。
    """
    changes = category_changes()
    if not coin_categories or not changes:
        return None, None
    for name in coin_categories:
        if not name:
            continue
        slug = str(name).strip().lower().replace(" ", "-").replace("&", "and")
        if slug in changes:
            return name, changes[slug]
    return None, None


# ──────────────────── BTC 主导率趋势 ────────────────────


def dominance_change_pct(days: int = 30) -> float | None:
    """BTC 主导率近 `days` 天变化（**百分点**，不是相对百分比）。库里没历史返 None。

    主导率本身就是百分比，所以这里用**差值**而不是变化率：从 54% 到 57% = +3 个百分点。
    """
    def _produce():
        from crypto_intel_engine.store import read_market_series
        from data_engine.storage.database import get_session
        session = get_session()
        try:
            series = read_market_series(session, "btc_dominance", days=days + 10)
        finally:
            session.close()
        if len(series) <= days:
            return None
        return round(series[-1] - series[-(days + 1)], 3)

    return _cached(f"dominance_{days}", _produce)


# ──────────────────── 链上 TVL 趋势（有没有人真在用）────────────────────

# base 资产 → DefiLlama 链名。只列主流公链；不在表里的币（meme/纯代币）**没有链 TVL
# 概念**，直接跳过——硬给它算一个 TVL 只会制造假信号。
_CHAIN_NAMES: dict[str, str] = {
    "ETH": "Ethereum", "SOL": "Solana", "BNB": "BSC", "AVAX": "Avalanche",
    "APT": "Aptos", "SUI": "Sui", "ARB": "Arbitrum", "OP": "OP Mainnet",
    "MATIC": "Polygon", "POL": "Polygon", "TRX": "Tron", "TON": "TON",
    "NEAR": "Near", "ATOM": "Cosmos", "DOT": "Polkadot", "INJ": "Injective",
    "FTM": "Fantom", "CRO": "Cronos", "KAVA": "Kava", "SEI": "Sei",
}


def chain_tvl_change_pct(base_asset: str, days: int = 30) -> float | None:
    """某公链 TVL 近 `days` 天变化 %。非公链资产（BTC/meme/纯代币）返 None。"""
    chain = _CHAIN_NAMES.get((base_asset or "").upper())
    if not chain:
        return None

    def _produce():
        from acquisition.markets.crypto_onchain import chain_tvl_history
        rows = chain_tvl_history(chain, limit_days=days + 10)
        return _pct_change([r["tvl"] for r in rows], days)

    return _cached(f"tvl_{chain}_{days}", _produce)


# ──────────────────── 宏观（美元 / 纳指）────────────────────

# yfinance 代码：美元指数 / 纳指综合
_MACRO_TICKERS = {"dxy": "DX-Y.NYB", "nasdaq": "^IXIC"}


def macro_context(days: int = 20) -> dict[str, float] | None:
    """美元指数与纳指近 `days` 交易日变化 % —— 加密的风险偏好背景板。

    ⚠️ 走 `yf_batch` 门面并**必须**抢 `yahoo_job_lock`（跨 job 互斥，见 GOTCHAS）；
    抢不到锁就返回 None（本轮不带宏观，不阻塞、不排队等）。
    """
    def _produce():
        from datetime import date, timedelta

        from acquisition.markets.yf_batch import download_daily_history, yahoo_job_lock

        with yahoo_job_lock("crypto 宏观上下文") as ok:
            if not ok:
                return None
            start = (date.today() - timedelta(days=days * 2 + 20)).isoformat()
            raw = download_daily_history(list(_MACRO_TICKERS.values()), start)
            if raw is None or raw.empty:
                return None
            out: dict[str, float] = {}
            for key, ticker in _MACRO_TICKERS.items():
                try:
                    closes = raw[ticker]["Close"].dropna().tolist()
                except (KeyError, TypeError):
                    continue
                chg = _pct_change(closes, days)
                if chg is not None:
                    out[f"{key}_change_pct"] = chg
            return out or None

    return _cached(f"macro_{days}", _produce)
