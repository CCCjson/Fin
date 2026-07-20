"""币安合约（U 本位永续）衍生品数据 —— 现货波段的择时探照灯。

Jason 只做**现货**，但合约市场的 **资金费率 / 未平仓 / 多空持仓比** 是判断现货
何时进出的顶级情绪信号，且**币安免费给、无需 API key**：

- **资金费率 funding rate**：多空谁在付钱给谁。极端正 = 多头拥挤过热（回调风险），
  转负 = 市场恐慌（往往是波段更好的进场点）。
- **未平仓 OI**：杠杆总量。配合价格看突破是真放量还是要爆仓。
- **多空持仓比**：散户仓位方向，常做反向指标。

## 出网合规

与 `crypto.py` 同：走 `channels.make_session(Channel.OVERSEAS)`，绕开国内快代理池，
不裸 import。symbol 出网前过 `to_binance_symbol()` 剥 `.BN`（永续合约 symbol 与现货
同名，如 BTCUSDT）。

## 端点基址

合约行情在 fapi.binance.com（U 本位）。`/futures/data/*` 统计端点也在此。可用
.env `BINANCE_FAPI_BASE` 覆盖（如遇地域限制）。
"""
import os
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from acquisition.channels import Channel, make_session
from common.market import to_binance_symbol

_FAPI_BASE = os.getenv("BINANCE_FAPI_BASE", "https://fapi.binance.com").rstrip("/")


def _get(path: str, params: dict[str, Any], timeout: int = 30) -> Any:
    """GET 币安合约公开接口。走 OVERSEAS 通道。"""
    session = make_session(Channel.OVERSEAS)
    resp = session.get(f"{_FAPI_BASE}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _ms_to_iso(ms: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def get_funding_rate_history(symbol: str, limit: int = 30) -> list[dict]:
    """历史资金费率（每 8 小时一条）。返回 [{time, funding_rate}, ...] 时间升序。"""
    bn = to_binance_symbol(symbol)
    rows = _get("/fapi/v1/fundingRate", {"symbol": bn, "limit": limit}) or []
    return [
        {"time": _ms_to_iso(r.get("fundingTime")),
         "funding_rate": float(r.get("fundingRate", 0))}
        for r in rows
    ]


def get_current_funding(symbol: str) -> dict:
    """当前资金费率 + 标记价 + 下次结算时间（premiumIndex）。"""
    bn = to_binance_symbol(symbol)
    d = _get("/fapi/v1/premiumIndex", {"symbol": bn}) or {}
    return {
        "symbol": f"{bn}.BN",
        "funding_rate": float(d.get("lastFundingRate", 0)),
        "mark_price": float(d.get("markPrice", 0)),
        "next_funding_time": _ms_to_iso(d.get("nextFundingTime")),
    }


def get_open_interest_hist(symbol: str, period: str = "1d", limit: int = 30) -> list[dict]:
    """未平仓历史。period ∈ {5m,15m,30m,1h,2h,4h,6h,12h,1d}。返回 [{time, oi, oi_value}]。"""
    bn = to_binance_symbol(symbol)
    rows = _get("/futures/data/openInterestHist",
                {"symbol": bn, "period": period, "limit": limit}) or []
    return [
        {"time": _ms_to_iso(r.get("timestamp")),
         "oi": float(r.get("sumOpenInterest", 0)),
         "oi_value": float(r.get("sumOpenInterestValue", 0))}
        for r in rows
    ]


def get_long_short_ratio(symbol: str, period: str = "1d", limit: int = 30) -> list[dict]:
    """多空账户持仓比（散户仓位方向，常做反向指标）。返回 [{time, ratio, long_pct, short_pct}]。"""
    bn = to_binance_symbol(symbol)
    rows = _get("/futures/data/globalLongShortAccountRatio",
                {"symbol": bn, "period": period, "limit": limit}) or []
    return [
        {"time": _ms_to_iso(r.get("timestamp")),
         "ratio": float(r.get("longShortRatio", 0)),
         "long_pct": float(r.get("longAccount", 0)),
         "short_pct": float(r.get("shortAccount", 0))}
        for r in rows
    ]


def get_derivatives_snapshot(symbol: str) -> dict:
    """一次性给现货波段用的衍生品情绪快照：当前资金费率 + 最新 OI + 最新多空比。

    单个子项失败不拖垮整体（返回 None），调用方按需展示。
    """
    snap: dict[str, Any] = {"symbol": f"{to_binance_symbol(symbol)}.BN"}
    try:
        snap["funding"] = get_current_funding(symbol)
    except Exception as e:  # noqa: BLE001 — 单项失败按空处理
        logger.warning(f"资金费率抓取失败 {symbol}: {e}")
        snap["funding"] = None
    try:
        oi = get_open_interest_hist(symbol, period="1d", limit=1)
        snap["open_interest"] = oi[-1] if oi else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"未平仓抓取失败 {symbol}: {e}")
        snap["open_interest"] = None
    try:
        ls = get_long_short_ratio(symbol, period="1d", limit=1)
        snap["long_short"] = ls[-1] if ls else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"多空比抓取失败 {symbol}: {e}")
        snap["long_short"] = None
    return snap
