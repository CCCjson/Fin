"""链上 / 代币经济学数据源 —— DefiLlama 免费档（稳定币供应 · TVL · **真解锁时间表**）。

## 为什么是这三样

- **稳定币总供应**：加密世界的「水位」。法币要买币，绝大多数先换成 USDT/USDC —— 稳定币
  总量在扩张 = 新钱在进场，收缩 = 在撤离。这是零 key 能拿到的**最好的资金流代理**
  （BTC 现货 ETF 净流入 2026-07-21 实测无免费源：DefiLlama 无端点、Farside 被 Cloudflare 挡）。
- **链 TVL**：这条链上真锁了多少钱 = 有没有人真在用（排雷层「空壳项目」的照妖镜）。
- **解锁时间表**：大额解锁是砸盘前兆，排雷层的核心信号。此前项目用 FDV/市值比**猜**稀释，
  现在能拿到**真日程**。

## 免费边界（实测 2026-07-21，别踩）

- ⛔ `api.llama.fi/emissions` 与 `/emissionsBreakdown` 已转**付费**（HTTP 402）。
- ✅ 前端自用的静态数据集 CDN `defillama-datasets.llama.fi/emissions/{slug}` **仍免费**，
  且数据更全（每类持有人的日频累计解锁曲线 + 离散 cliff 事件，排到 2032 年）。
- ⚠️ `emissionsProtocolsList` **不权威**：`arbitrum` 不在清单里，但 `emissions/arbitrum`
  有数据。所以一律**直接试端点、404 当作无解锁数据**，不拿清单做预检。
- 深度链上（MVRV/SOPR/巨鲸/交易所净流）免费档拿不到，如实标缺失，不用劣质代理硬凑。

## 出网合规

与 `crypto.py` 同：走 `channels.make_session(Channel.OVERSEAS)`，不裸 import。
"""
from datetime import date, datetime, timezone
from typing import Any

from loguru import logger

from acquisition.channels import Channel, make_session

_LLAMA = "https://api.llama.fi"
_DATASETS = "https://defillama-datasets.llama.fi"
_STABLECOINS = "https://stablecoins.llama.fi"


def _get(url: str, params: dict | None = None, timeout: int = 30) -> Any:
    """GET DefiLlama（免费档，无需 key）。走 OVERSEAS 通道。"""
    session = make_session(Channel.OVERSEAS)
    resp = session.get(url, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _ts_to_date(ts: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(float(ts)), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


# ──────────────────── 稳定币供应（资金流「水位」）────────────────────


def stablecoin_supply(limit_days: int = 120) -> list[dict]:
    """全市场稳定币流通总额时序（日频，新在末）。返回 [{date, total_usd}]。

    取 `totalCirculatingUSD.peggedUSD`（锚定美元的流通量，已折美元）。
    """
    rows = _get(f"{_STABLECOINS}/stablecoincharts/all", {"stablecoin": 1}) or []
    out: list[dict] = []
    for r in rows[-limit_days:]:
        d = _ts_to_date(r.get("date"))
        total = (r.get("totalCirculatingUSD") or {}).get("peggedUSD")
        if d is not None and isinstance(total, (int, float)):
            out.append({"date": d, "total_usd": float(total)})
    return out


# ──────────────────── TVL（有没有人真在用）────────────────────


def chain_tvl_history(chain: str, limit_days: int = 120) -> list[dict]:
    """某条链的历史 TVL（日频，新在末）。chain 用 DefiLlama 链名（Ethereum/Solana/Aptos）。"""
    rows = _get(f"{_LLAMA}/v2/historicalChainTvl/{chain}") or []
    out: list[dict] = []
    for r in rows[-limit_days:]:
        d = _ts_to_date(r.get("date"))
        tvl = r.get("tvl")
        if d is not None and isinstance(tvl, (int, float)):
            out.append({"date": d, "tvl": float(tvl)})
    return out


def protocol_tvl(slug: str) -> float | None:
    """某协议当前 TVL（美元）。查不到返 None。"""
    try:
        v = _get(f"{_LLAMA}/tvl/{slug}")
        return float(v) if isinstance(v, (int, float)) else None
    except Exception as e:  # noqa: BLE001 — 协议不存在/限速，按缺失处理
        logger.debug(f"protocol_tvl 取数失败 {slug}: {e}")
        return None


# ──────────────────── 解锁时间表（砸盘前兆）────────────────────


def fetch_unlock_payload(slug: str) -> dict | None:
    """拉某项目的解锁原始数据（静态数据集 CDN，免费）。无该项目/取数失败返 None。

    slug 通常等于 CoinGecko id（`aptos`/`arbitrum`）。清单不权威，直接试端点。
    """
    try:
        d = _get(f"{_DATASETS}/emissions/{slug}")
        return d if isinstance(d, dict) else None
    except Exception as e:  # noqa: BLE001 — 404 = 该币无解锁数据（BTC 这类老币正常没有）
        logger.debug(f"无解锁数据 {slug}: {e}")
        return None


def parse_unlock_events(payload: dict, *, only_future: bool = True,
                        now_ts: float | None = None) -> list[dict]:
    """纯函数：解锁原始数据 → 离散解锁事件 [{unlock_date, amount, category, unlock_type}]。

    吃 `metadata.events`（cliff/linear 变更等离散事件），供落 `TokenUnlock` 表。
    `noOfTokens` 是数组（linear 变更事件给 [旧值, 新值]），取**最后一个**为本次数量。
    """
    events = ((payload or {}).get("metadata") or {}).get("events") or []
    ref = now_ts if now_ts is not None else datetime.now(tz=timezone.utc).timestamp()
    out: list[dict] = []
    for e in events:
        ts = e.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        if only_future and ts <= ref:
            continue
        d = _ts_to_date(ts)
        tokens = e.get("noOfTokens") or []
        amount = None
        if isinstance(tokens, list) and tokens:
            try:
                amount = float(tokens[-1])
            except (TypeError, ValueError):
                amount = None
        if d is None:
            continue
        out.append({
            "unlock_date": d,
            "amount": amount,
            "category": str(e.get("category") or "unknown"),
            "unlock_type": str(e.get("unlockType") or ""),
        })
    return sorted(out, key=lambda r: r["unlock_date"])


def parse_upcoming_unlock_tokens(payload: dict, *, days: int = 30,
                                 now_ts: float | None = None) -> float | None:
    """纯函数：未来 `days` 天将解锁多少枚币（累计曲线差分，各类别求和）。

    `documentedData.data[*].data[*].unlocked` 实测是**单调不减的累计值**且带未来点，
    所以「未来 N 天解锁量」= (t+N 时刻累计) − (当下累计)。全类别无有效点时返 None。
    """
    cats = ((payload or {}).get("documentedData") or {}).get("data") or []
    ref = now_ts if now_ts is not None else datetime.now(tz=timezone.utc).timestamp()
    horizon = ref + days * 86400
    total = 0.0
    seen = False
    for cat in cats:
        pts = cat.get("data") or []
        now_val = future_val = None
        for p in pts:
            ts, val = p.get("timestamp"), p.get("unlocked")
            if not isinstance(ts, (int, float)) or not isinstance(val, (int, float)):
                continue
            if ts <= ref:
                now_val = float(val)
            if ts <= horizon:
                future_val = float(val)
        if now_val is not None and future_val is not None:
            total += max(0.0, future_val - now_val)
            seen = True
    return round(total, 4) if seen else None


def get_unlock_summary(slug: str, circulating_supply: float | None = None,
                       *, days: int = 30) -> dict | None:
    """某币的解锁体检：未来 N 天解锁量 + 占流通比例 + 未来事件列表。无数据返 None。

    `pct_of_supply` 是排雷层真正要用的量（占流通 %，>3% 通常够砸出明显抛压）。
    """
    payload = fetch_unlock_payload(slug)
    if not payload:
        return None
    tokens = parse_upcoming_unlock_tokens(payload, days=days)
    pct = None
    if tokens is not None and circulating_supply:
        pct = round(tokens / circulating_supply * 100, 3)
    return {
        "slug": slug,
        "days": days,
        "upcoming_tokens": tokens,
        "pct_of_supply": pct,
        "events": parse_unlock_events(payload),
    }
