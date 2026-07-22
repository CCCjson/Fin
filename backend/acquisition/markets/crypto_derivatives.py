"""币安合约（U 本位永续）衍生品数据 —— 现货波段的择时探照灯。

Jason 只做**现货**，但合约市场的 **资金费率 / 未平仓 / 多空持仓比** 是判断现货
何时进出的顶级情绪信号，且**币安免费给、无需 API key**：

- **资金费率 funding rate**：多空谁在付钱给谁。极端正 = 多头拥挤过热（回调风险），
  转负 = 市场恐慌（往往是波段更好的进场点）。
- **未平仓 OI**：杠杆总量。配合价格看突破是真放量还是要爆仓。
- **多空持仓比**：散户仓位方向，常做反向指标。
- **大户持仓比**：前 20% 保证金账户按**持仓量**的多空比，顺向的聪明钱（对照散户账户比）。
- **主动买卖量比 taker**：谁在吃单（进攻方向），比持仓比更即时。
- **期现基差 basis**：永续相对现货指数的溢价/贴水，是资金费率的「因」。

⚠️ 全部端点 2026-07-21 实测**零 key 可取**；单项失败只降级不抛（见 `_safe`）。

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


def get_top_trader_position_ratio(symbol: str, period: str = "1d", limit: int = 30) -> list[dict]:
    """**大户**持仓量多空比（按持仓量加权，不是按人头）。返回 [{time, ratio, long_pct, short_pct}]。

    与 `get_long_short_ratio`（全市场散户**账户数**比）的关键区别：这条是保证金账户排名前
    20% 的大户、且按**持仓量**计。散户账户比常做反向指标，大户持仓比更接近顺向的聪明钱。
    """
    bn = to_binance_symbol(symbol)
    rows = _get("/futures/data/topLongShortPositionRatio",
                {"symbol": bn, "period": period, "limit": limit}) or []
    return [
        {"time": _ms_to_iso(r.get("timestamp")),
         "ratio": float(r.get("longShortRatio", 0)),
         "long_pct": float(r.get("longAccount", 0)),
         "short_pct": float(r.get("shortAccount", 0))}
        for r in rows
    ]


def get_taker_flow(symbol: str, period: str = "1d", limit: int = 30) -> list[dict]:
    """合约**主动**买卖量比（吃单方向 = 攻击性资金流）。返回 [{time, ratio, buy_vol, sell_vol}]。

    ratio = 主动买入量/主动卖出量。>1 = 买方在主动吃卖单（进攻），<1 = 卖方在砸盘。
    比持仓比更「即时」：持仓比说的是谁站着，这条说的是谁在动手。
    """
    bn = to_binance_symbol(symbol)
    rows = _get("/futures/data/takerlongshortRatio",
                {"symbol": bn, "period": period, "limit": limit}) or []
    return [
        {"time": _ms_to_iso(r.get("timestamp")),
         "ratio": float(r.get("buySellRatio", 0)),
         "buy_vol": float(r.get("buyVol", 0)),
         "sell_vol": float(r.get("sellVol", 0))}
        for r in rows
    ]


def get_basis(symbol: str, period: str = "1d", limit: int = 30) -> list[dict]:
    """期现基差（永续价 vs 现货指数价）。返回 [{time, basis_rate, basis, futures_price, index_price}]。

    basis_rate 正 = 永续溢价（多头愿意付溢价做多，情绪偏热）；负 = 贴水（恐慌/看空）。
    与资金费率同源但更直接：费率是溢价的**结果**，基差是溢价**本身**。
    """
    bn = to_binance_symbol(symbol)
    rows = _get("/futures/data/basis",
                {"pair": bn, "contractType": "PERPETUAL", "period": period, "limit": limit}) or []
    out = []
    for r in rows:
        try:
            out.append({
                "time": _ms_to_iso(r.get("timestamp")),
                "basis_rate": float(r.get("basisRate", 0)),
                "basis": float(r.get("basis", 0)),
                "futures_price": float(r.get("futuresPrice", 0)),
                "index_price": float(r.get("indexPrice", 0)),
            })
        except (TypeError, ValueError):
            continue
    return out


def _safe(label: str, symbol: str, fn, default=None):
    """取一项衍生品数据，失败只记 warning 返 default（单项挂不拖垮整张快照）。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — 单项失败按空处理
        logger.warning(f"{label}抓取失败 {symbol}: {e}")
        return default


def get_derivatives_snapshot(symbol: str, *, hist_days: int = 60) -> dict:
    """现货波段用的衍生品情绪快照 —— 当前值 + **历史序列**（历史给打分器算分位/变化率）。

    单个子项失败不拖垮整体（该键为 None/空），调用方按需展示。

    Keys:
        funding / open_interest / long_short：当前值（老字段，向后兼容不动）
        funding_history：资金费率历史（8h 一条），供分位打分
        oi_history：未平仓日线序列，供算变化率与「价量背离」
        taker_flow / top_trader / basis：主动吃单流 / 大户持仓比 / 期现基差（当前值）
    """
    snap: dict[str, Any] = {"symbol": f"{to_binance_symbol(symbol)}.BN"}

    snap["funding"] = _safe("资金费率", symbol, lambda: get_current_funding(symbol))
    # 60 天 ≈ 180 条（8h 一结算），够算分位；限速无压力
    snap["funding_history"] = _safe(
        "资金费率历史", symbol,
        lambda: get_funding_rate_history(symbol, limit=min(1000, hist_days * 3)), [])

    oi_hist = _safe("未平仓", symbol,
                    lambda: get_open_interest_hist(symbol, period="1d", limit=30), [])
    snap["oi_history"] = oi_hist
    snap["open_interest"] = oi_hist[-1] if oi_hist else None

    ls = _safe("多空比", symbol,
               lambda: get_long_short_ratio(symbol, period="1d", limit=1), [])
    snap["long_short"] = ls[-1] if ls else None

    tt = _safe("大户持仓比", symbol,
               lambda: get_top_trader_position_ratio(symbol, period="1d", limit=1), [])
    snap["top_trader"] = tt[-1] if tt else None

    tf = _safe("主动买卖流", symbol,
               lambda: get_taker_flow(symbol, period="1d", limit=1), [])
    snap["taker_flow"] = tf[-1] if tf else None

    bs = _safe("期现基差", symbol,
               lambda: get_basis(symbol, period="1d", limit=1), [])
    snap["basis"] = bs[-1] if bs else None

    return snap
