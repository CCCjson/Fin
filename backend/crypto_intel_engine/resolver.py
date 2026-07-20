"""symbol → CoinGecko id 解析 —— 排雷层取数前的一步。

项目内加密 symbol 是 `BTCUSDT.BN`，CoinGecko 用自己的 id（`bitcoin`/`ethereum`）。
主流币走**策展映射表**（准、无歧义）；表外的走 CoinGecko `/coins/list` 按 symbol
回退（有 symbol 撞车风险，取第一个并在结果标 `ambiguous`）。

`base_asset()`：从交易对剥出基础币（`BTCUSDT.BN` → `BTC`）。默认只认 USDT 计价。
"""
from functools import lru_cache

from common.market import to_binance_symbol

_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "ETH", "BNB")

# 主流币策展表：base 资产 → coingecko id。表外走 /coins/list 回退。
_CURATED: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "TRX": "tron",
    "AVAX": "avalanche-2", "DOT": "polkadot", "LINK": "chainlink", "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token", "LTC": "litecoin", "BCH": "bitcoin-cash",
    "UNI": "uniswap", "ATOM": "cosmos", "XLM": "stellar", "ETC": "ethereum-classic",
    "FIL": "filecoin", "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "NEAR": "near", "INJ": "injective-protocol", "SUI": "sui", "TON": "the-open-network",
    "SHIB": "shiba-inu", "PEPE": "pepe", "AAVE": "aave", "MKR": "maker",
}


def base_asset(symbol: str) -> str:
    """交易对 → 基础币：`BTCUSDT.BN` → `BTC`。剥 `.BN` 与计价后缀。"""
    s = to_binance_symbol(symbol).upper()
    for q in _QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    return s


@lru_cache(maxsize=1)
def _coingecko_index() -> dict[str, str]:
    """CoinGecko symbol(小写) → id 的回退索引（同 symbol 撞车时保留首个）。"""
    from acquisition.markets.crypto_intel import coingecko_list
    idx: dict[str, str] = {}
    for row in coingecko_list():
        sym = str(row.get("symbol", "")).lower()
        if sym and sym not in idx:
            idx[sym] = row.get("id", "")
    return idx


def resolve_coingecko_id(symbol: str) -> tuple[str | None, bool]:
    """symbol → (coingecko_id, ambiguous)。策展命中 ambiguous=False；回退命中=True；查不到=(None, False)。"""
    base = base_asset(symbol)
    if base in _CURATED:
        return _CURATED[base], False
    cid = _coingecko_index().get(base.lower())
    return (cid, True) if cid else (None, False)
