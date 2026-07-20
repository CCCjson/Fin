"""加密情报数据源门面 —— 排雷层用的免费源原始取数（CoinGecko / alternative.me）。

排雷层的业务打分在 `crypto_intel_engine/`；本模块只做**出网取数**（合规要求：出网
必须在 `acquisition/` 内，见 `tests/net/test_egress_single_entry.py`）。全部经
`channels.make_session(Channel.OVERSEAS)`，绕开国内快代理池，免费源无需 key。

## 免费源与限速

- **CoinGecko** free：`/coins/{id}`（一次拿 供应/市值/FDV/开发活跃度，排雷主力）、
  `/global`（BTC 主导率）、`/coins/list`（symbol→id 回退解析）。限速 ~10-30 次/分，
  排雷是低频调用，够用。可选 `.env COINGECKO_API_KEY` 走 demo/pro（走 header）。
- **alternative.me** `/fng`：恐慌贪婪指数（市场级情绪），无限速压力。

深度链上（TVL/解锁 DefiLlama、巨鲸 Glassnode/Nansen）v1 先不做——免费档薄且主流币
无解锁/无稀释，见 crypto_intel_engine 的 TODO。
"""
import os
from typing import Any

from acquisition.channels import Channel, make_session

_CG_BASE = os.getenv("COINGECKO_BASE", "https://api.coingecko.com/api/v3").rstrip("/")
_FNG_BASE = "https://api.alternative.me"


def _cg_get(path: str, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    """GET CoinGecko。有 COINGECKO_API_KEY 则带 demo header（提高限速）。"""
    session = make_session(Channel.OVERSEAS)
    headers = {}
    key = os.getenv("COINGECKO_API_KEY", "")
    if key:
        headers["x-cg-demo-api-key"] = key
    resp = session.get(f"{_CG_BASE}{path}", params=params or {}, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def coingecko_coin(coin_id: str) -> dict:
    """一次拿某币的 供应/市值/FDV/开发活跃度（排雷主力，一个请求全给）。"""
    return _cg_get(f"/coins/{coin_id}", {
        "localization": "false", "tickers": "false", "market_data": "true",
        "community_data": "false", "developer_data": "true", "sparkline": "false",
    })


def coingecko_global() -> dict:
    """全市场概览：BTC 主导率、总市值、市值变化（`data` 下）。"""
    return _cg_get("/global").get("data", {})


def coingecko_list() -> list[dict]:
    """全部币的 [{id, symbol, name}]，供 symbol→id 回退解析（有 symbol 撞车，取市值最大另判）。"""
    return _cg_get("/coins/list") or []


def fear_greed_index(limit: int = 1) -> list[dict]:
    """恐慌贪婪指数（市场级情绪）。返回 [{value:int, classification, timestamp}] 新→旧。"""
    session = make_session(Channel.OVERSEAS)
    resp = session.get(f"{_FNG_BASE}/fng/", params={"limit": limit}, timeout=20)
    resp.raise_for_status()
    data = resp.json() or {}
    out = []
    for r in data.get("data", []):
        out.append({
            "value": int(r.get("value", 0)),
            "classification": r.get("value_classification", ""),
            "timestamp": r.get("timestamp"),
        })
    return out
