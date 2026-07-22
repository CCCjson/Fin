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
import threading
import time
from typing import Any

from acquisition.channels import Channel, make_session

_CG_BASE = os.getenv("COINGECKO_BASE", "https://api.coingecko.com/api/v3").rstrip("/")
_FNG_BASE = "https://api.alternative.me"

# CoinGecko free 档实测约 10-30 次/分，超了直接 429。情报刷新一轮要扫几十个币，
# 不限速必然打爆（2026-07-22 实测：0.1s 间隔跑 28 个币，一半吃 429）。
# 进程级最小间隔，跨线程生效；配了 key 的话额度高，可用 .env 调小。
_CG_MIN_INTERVAL = float(os.getenv("COINGECKO_MIN_INTERVAL", "2.5"))
# 429 最多重试几次（退避 2.5s → 10s → 40s）。慢档一天只跑一次，等得起。
_CG_MAX_RETRIES = int(os.getenv("COINGECKO_MAX_RETRIES", "2"))
_cg_lock = threading.Lock()
_cg_last_at = 0.0


def _cg_pace() -> None:
    """保证两次 CoinGecko 请求间隔 ≥ `_CG_MIN_INTERVAL`（防 429）。"""
    global _cg_last_at
    if _CG_MIN_INTERVAL <= 0:
        return
    with _cg_lock:
        wait = _cg_last_at + _CG_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _cg_last_at = time.monotonic()


def _cg_get(path: str, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    """GET CoinGecko（自带限速节流 + 429 退避重试）。

    有 COINGECKO_API_KEY 则带 demo header（额度更高，`.env` 配了就自动生效）。
    429 是**滚动窗口封禁**而非简单计数，所以退避要够狠（指数级），退完仍 429 才抛。
    """
    key = os.getenv("COINGECKO_API_KEY", "")
    headers = {"x-cg-demo-api-key": key} if key else {}
    last_exc: Exception | None = None

    for attempt in range(_CG_MAX_RETRIES + 1):
        _cg_pace()
        session = make_session(Channel.OVERSEAS)
        resp = session.get(f"{_CG_BASE}{path}", params=params or {},
                           headers=headers, timeout=timeout)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp.json()
        # 429：退避后重试（2.5s → 10s → 40s …），给滚动窗口留出恢复时间
        backoff = _CG_MIN_INTERVAL * (4 ** attempt)
        last_exc = RuntimeError(f"CoinGecko 429 限速（已重试 {attempt} 次）: {path}")
        if attempt < _CG_MAX_RETRIES:
            time.sleep(backoff)

    raise last_exc


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


def coingecko_categories() -> list[dict]:
    """板块分类行情（叙事轮动）：[{id, name, market_cap, market_cap_change_24h, volume_24h}]。

    加密是**叙事驱动**的市场——钱在 AI / DePIN / meme / L2 之间轮动，判断手上这个币
    所属赛道是在被买还是在被抛，比单看它自己的 K 线多一层信息。CoinGecko free 可取。
    """
    rows = _cg_get("/coins/categories") or []
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "market_cap": r.get("market_cap"),
            "market_cap_change_24h": r.get("market_cap_change_24h"),
            "volume_24h": r.get("volume_24h"),
        })
    return out


def fear_greed_index(limit: int = 1) -> list[dict]:
    """恐慌贪婪指数（市场级情绪）。返回 [{value:int, classification, timestamp}] 新→旧。"""
    session = make_session(Channel.OVERSEAS)
    resp = session.get(f"{_FNG_BASE}/fng/", params={"limit": limit}, timeout=20)
    resp.raise_for_status()
    data = resp.json() or {}
    out = []
    for r in data.get("data", []):
        # ⛔ 缺值必须是 None，绝不能兜底成 0。0 在这个量表上是「极度恐惧」这一端，
        # 而打分器对极度恐惧给 **+8 的看多加分**（历史上常是波段买点）——
        # 于是「字段没拿到」会被翻译成「市场恐慌到极点，加仓信号」。
        # 缺数据往量表极值端映射、方向还偏多，是这类 bug 里最坏的一种。
        raw = r.get("value")
        try:
            value = int(raw) if raw is not None and str(raw).strip() != "" else None
        except (TypeError, ValueError):
            value = None
        out.append({
            "value": value,
            "classification": r.get("value_classification", ""),
            "timestamp": r.get("timestamp"),
        })
    return out
