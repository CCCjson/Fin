"""加密货币（币安现货）数据获取器 —— 基于币安公开 REST 行情接口。

## 出网合规（改前必读）

币安是**境外源**，绝不走国内快代理池（那条铁律只管国内抓取）。本模块统一经
`acquisition.channels.make_session(Channel.OVERSEAS)` 出网：本地能直连就直连、
不通走 Shadowrocket/Clash 自适配。不裸 `import requests`/币安 SDK —— 出网单入口
门禁 `tests/net/test_egress_single_entry.py` 在 AST 层只豁免 `acquisition`/`net`
顶包，本模块正在 `acquisition` 内，session 由 channels 造，合规。

## symbol 双形态

项目内加密货币统一带 `.BN` 后缀存（`BTCUSDT.BN`，供 `infer_market_from_symbol`
推断市场），但币安 REST 只认裸交易对（`BTCUSDT`）。**任何出网调用前都先过
`to_binance_symbol()` 剥后缀**，与港股 `to_yf_symbol` 同一模式。

## 只读行情，不碰密钥

klines / exchangeInfo / ticker 都是公开接口，**不需要 API key**。交易下单（需
key）在 `trading_engine/brokers/binance_broker.py`，与本行情模块分离。
"""
import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger

from acquisition.channels import Channel, make_session
from acquisition.markets.base import BaseFetcher, MarketDataRequest, MarketDataResponse
from common.market import CRYPTO, to_binance_symbol

# 公开行情基址。默认主站；如遇地域限制可在 .env 设 BINANCE_REST_BASE
# 为 https://data-api.binance.vision（币安官方公开行情镜像，无需鉴权）。
_SPOT_BASE = os.getenv("BINANCE_REST_BASE", "https://api.binance.com").rstrip("/")

# 币安 K 线 limit 单次上限
_KLINES_LIMIT = 1000

# 频率 → 币安 interval。项目内部主用 1d；4h 供多周期确认（`crypto_bars` 表）。
_FREQ_MAP = {
    "1d": "1d", "1day": "1d", "daily": "1d",
    "1w": "1w", "1week": "1w", "weekly": "1w",
    "1M": "1M", "1mon": "1M", "monthly": "1M",
    "4h": "4h", "4hour": "4h", "1h": "1h", "1hour": "1h",
}


class CryptoFetcher(BaseFetcher):
    """加密货币（币安现货）数据获取器。"""

    def __init__(self, config: dict = None):
        super().__init__(config or {})
        self.source = "binance"

    # ── 出网 ────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict[str, Any]) -> Any:
        """GET 币安公开接口，返回解析后的 JSON。走 OVERSEAS 通道。"""
        session = make_session(Channel.OVERSEAS)
        resp = session.get(f"{_SPOT_BASE}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ── 日线 ────────────────────────────────────────────────────────────
    def fetch_daily(self, request: MarketDataRequest) -> MarketDataResponse:
        """获取日线数据。symbol 传项目内形态（BTCUSDT.BN），内部剥后缀调币安。"""
        bn_symbol = to_binance_symbol(request.symbol)
        interval = _FREQ_MAP.get(request.freq, "1d")
        try:
            start_ms = _date_to_ms(request.start_date)
            end_ms = _date_to_ms(request.end_date, end_of_day=True)
            rows = self._fetch_klines(bn_symbol, interval, start_ms, end_ms)
            df = _klines_to_df(rows)

            if df.empty:
                # 盘中增量更新时这是常态：只剩今天那根未收盘的 bar，被刻意丢掉了
                logger.debug(f"{request.symbol} 无已收盘的新 K 线")
                return MarketDataResponse(
                    symbol=request.symbol, market=CRYPTO, data=pd.DataFrame(),
                    metadata={"source": self.source, "message": "No data"},
                )

            logger.info(f"成功获取 {request.symbol} 的 {len(df)} 条数据")
            return MarketDataResponse(
                symbol=request.symbol, market=CRYPTO, data=df,
                metadata={
                    "source": self.source,
                    "fetched_at": datetime.now().isoformat(),
                    "records": len(df),
                },
            )
        except Exception as e:
            logger.error(f"获取 {request.symbol} 数据失败: {e}")
            raise

    def _fetch_klines(self, bn_symbol: str, interval: str,
                      start_ms: int, end_ms: int) -> list[list]:
        """分页拉取币安 K 线（单次上限 1000 根，按 openTime 游标翻页）。"""
        out: list[list] = []
        cursor = start_ms
        while cursor <= end_ms:
            batch = self._get("/api/v3/klines", {
                "symbol": bn_symbol, "interval": interval,
                "startTime": cursor, "endTime": end_ms, "limit": _KLINES_LIMIT,
            })
            if not batch:
                break
            out.extend(batch)
            if len(batch) < _KLINES_LIMIT:
                break
            # 下一页从最后一根的 openTime + 1ms 开始，避免重复末根
            cursor = int(batch[-1][0]) + 1
        return out

    # ── 实时 ────────────────────────────────────────────────────────────
    def fetch_realtime(self, symbols: list[str]) -> list[dict]:
        """批量实时价 —— **按需**向币安要，不再一次拉全市场再客户端过滤。

        单个走 `?symbol=`，多个走 `?symbols=["A","B"]`（币安支持的紧凑 JSON 数组）。
        全市场拉取（几千对、几百 KB）在热报价路径上白烧带宽/延迟/代理额度。
        """
        try:
            if not symbols:
                return []
            bn_list = [to_binance_symbol(s) for s in symbols]
            if len(bn_list) == 1:
                data = self._get("/api/v3/ticker/price", {"symbol": bn_list[0]})
                rows = [data] if isinstance(data, dict) else (data or [])
            else:
                symbols_param = json.dumps(bn_list, separators=(",", ":"))
                data = self._get("/api/v3/ticker/price", {"symbols": symbols_param})
                rows = data or []
            out = []
            for row in rows:
                bn = row.get("symbol")
                if not bn:
                    continue
                out.append({
                    "symbol": f"{bn}.BN",
                    "price": float(row.get("price", 0)),
                    "market": CRYPTO,
                })
            return out
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            raise

    # ── 多周期 K 线（4h 确认用）────────────────────────────────────────
    def fetch_bars(self, symbol: str, interval: str = "4h", limit: int = 500) -> list[dict]:
        """拉近 `limit` 根指定周期 K 线（不分页，最新在末）。

        与 `fetch_daily` 的区别：这条按**根数**取最新的、保留 `open_time` 时间戳精度
        （日线按日期落 `daily_quotes`，4h 按 open_time 落 `crypto_bars`）。
        额外保留币安自带的 `taker_buy_base`（主动买入量）与 `quote_volume`。
        """
        bn = to_binance_symbol(symbol)
        iv = _FREQ_MAP.get(interval, interval)
        rows = self._get("/api/v3/klines",
                         {"symbol": bn, "interval": iv, "limit": min(limit, _KLINES_LIMIT)}) or []
        return [_kline_to_bar(symbol, iv, k) for k in rows]

    # ── 流动性 / 盘口 ───────────────────────────────────────────────────
    def get_ticker_24hr(self, symbol: str) -> dict:
        """24 小时行情统计（流动性闸用 `quote_volume`，即 24h 成交额 USDT）。"""
        bn = to_binance_symbol(symbol)
        d = self._get("/api/v3/ticker/24hr", {"symbol": bn}) or {}
        return {
            "symbol": f"{bn}.BN",
            "quote_volume": float(d.get("quoteVolume") or 0),
            "volume": float(d.get("volume") or 0),
            "trades": int(d.get("count") or 0),
            "price_change_pct": float(d.get("priceChangePercent") or 0),
            "high": float(d.get("highPrice") or 0),
            "low": float(d.get("lowPrice") or 0),
        }

    def get_order_book(self, symbol: str, limit: int = 100) -> dict:
        """盘口深度（买卖各 `limit` 档）。返回 {bids, asks}，每档 [价, 量] 均为 float。"""
        bn = to_binance_symbol(symbol)
        d = self._get("/api/v3/depth", {"symbol": bn, "limit": limit}) or {}
        def _side(rows):
            out = []
            for r in rows or []:
                try:
                    out.append([float(r[0]), float(r[1])])
                except (TypeError, ValueError, IndexError):
                    continue
            return out
        return {"symbol": f"{bn}.BN", "bids": _side(d.get("bids")), "asks": _side(d.get("asks"))}

    # ── 校验 / 搜索 ─────────────────────────────────────────────────────
    def validate_symbol(self, symbol: str) -> bool:
        """校验交易对是否存在（查 exchangeInfo）。"""
        bn = to_binance_symbol(symbol)
        try:
            data = self._get("/api/v3/exchangeInfo", {"symbol": bn})
            return bool((data or {}).get("symbols"))
        except Exception:
            return False

    def search_symbol(self, keyword: str) -> list[dict]:
        """按关键词在全交易对里模糊搜（base/quote 资产或交易对名包含关键词）。"""
        kw = keyword.strip().upper()
        if not kw:
            return []
        return [row for row in self.get_stock_list() if kw in row["symbol"].upper()
                or kw in row["name"].upper()]

    # ── 交易对清单 ───────────────────────────────────────────────────────
    def get_stock_list(self, quote_assets: tuple = ("USDT",)) -> list[dict]:
        """获取币安现货交易对清单，用于填充 StockInfo 表。

        默认只保留 USDT 计价对（`quote_assets` 可放宽），且只要状态为 TRADING 的现货对。
        symbol 落库带 `.BN` 后缀（`BTCUSDT.BN`），name 用可读的 `BASE/QUOTE`（`BTC/USDT`）。
        """
        logger.info("获取币安现货交易对清单...")
        data = self._get("/api/v3/exchangeInfo", {})
        symbols = (data or {}).get("symbols") or []
        result = []
        for s in symbols:
            if s.get("status") != "TRADING":
                continue
            if not s.get("isSpotTradingAllowed", True):
                continue
            quote = s.get("quoteAsset", "")
            if quote_assets and quote not in quote_assets:
                continue
            base = s.get("baseAsset", "")
            bn = s.get("symbol", "")
            if not bn or not base:
                continue
            result.append({
                "symbol": f"{bn}.BN",
                "name": f"{base}/{quote}",
                "market": CRYPTO,
            })
        if not result:
            raise RuntimeError("币安交易对一页都没拉到，检查代理/网络")
        logger.info(f"获取到 {len(result)} 个币安现货交易对")
        return result


# ── 纯函数：K 线 → DataFrame / 落库记录 ────────────────────────────────────
def _date_to_ms(date_str: str, *, end_of_day: bool = False) -> int:
    """'YYYY-MM-DD' → 币安要的毫秒时间戳（UTC）。end_of_day 取当天 23:59:59。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp() * 1000)


def _kline_to_bar(symbol: str, interval: str, k: list) -> dict:
    """币安 kline 数组 → `crypto_bars` 记录。

    币安 kline 12 字段：[openTime, o, h, l, c, volume, closeTime, quoteVolume,
    trades, takerBuyBase, takerBuyQuote, ignore]。第 9/10 位的**主动买入量**是
    免费的现货买压真数据（多数实现都把它丢了），这里留下来喂资金流维。
    """
    vol = float(k[5])
    taker_buy = float(k[9])
    return {
        "symbol": symbol,
        "interval": interval,
        "open_time": datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),
        "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]),
        "volume": vol,
        "quote_volume": float(k[7]),
        "trades": int(k[8]),
        "taker_buy_base": taker_buy,
        # 主动买入占比：>0.5 买方主动吃单，<0.5 卖方主动砸盘。零成交时留 None 不造 0.5 假中性
        "taker_buy_ratio": round(taker_buy / vol, 4) if vol > 0 else None,
    }


def estimate_slippage(order_book: dict, notional_usdt: float, side: str = "BUY") -> dict | None:
    """扫单簿算「吃掉 `notional_usdt` 名义额」的实测滑点（纯函数，可离线测）。

    BUY 吃 asks、SELL 吃 bids，逐档累加到目标金额，算成交均价相对最优价的偏离。
    这是把 `CostModel.slippage_pct` 从**拍脑袋的 0.05%** 换成**当下真实盘口**的关键。

    Returns:
        {slippage_pct, avg_price, best_price, filled_notional, exhausted} —
        `exhausted=True` 表示单簿档位吃光仍没凑够金额（流动性不足，调用方应视为高风险）；
        单簿为空返 None。
    """
    levels = (order_book or {}).get("asks" if side == "BUY" else "bids") or []
    if not levels or notional_usdt <= 0:
        return None
    best = levels[0][0]
    if best <= 0:
        return None

    remaining = notional_usdt
    cost = 0.0
    qty = 0.0
    for price, amount in levels:
        level_notional = price * amount
        take = min(level_notional, remaining)
        if take <= 0:
            break
        cost += take
        qty += take / price
        remaining -= take
        if remaining <= 0:
            break

    if qty <= 0:
        return None
    avg = cost / qty
    # BUY 买贵了为正滑点，SELL 卖便宜了也记为正（成本方向统一）
    slip = (avg - best) / best if side == "BUY" else (best - avg) / best
    return {
        "slippage_pct": round(max(0.0, slip), 6),
        "avg_price": avg,
        "best_price": best,
        "filled_notional": round(cost, 2),
        "exhausted": remaining > 0,
    }


def _klines_to_df(rows: list[list], include_unclosed: bool = False) -> pd.DataFrame:
    """币安 klines 数组 → 标准 DataFrame（列 date/open/high/low/close/volume）。

    币安 kline 结构：[openTime(ms), open, high, low, close, volume, closeTime, ...]，
    价量都是字符串，需转 float；openTime 是 UTC 当日 00:00。

    Args:
        include_unclosed: 是否保留**未收盘**的那根 K 线（默认丢弃）。

    ⛔ 默认丢弃是有原因的。crypto 是 7×24 市场、**没有收盘时点**，币安会把「当天正在走」
    的那根日线一并返回。股票市场靠「收盘后才更新」天然规避，crypto 不会。把未收盘的
    bar 写进 `daily_quotes` 会让下游 `SignalDetector` 的 MACD 金叉/布林突破在同一个 UTC 日
    内反复成立又消失（repaint），而策略每 30 分钟 tick 一次，唯一的防抖只有
    `cooldown_minutes`——等于按一个随时会变的「今天收盘价」反复下决策。
    判据用 `closeTime < 现在`，不做时区推算，对任何周期都成立。
    """
    if not rows:
        return pd.DataFrame()
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    recs = []
    for k in rows:
        open_ms = int(k[0])
        if not include_unclosed and int(k[6]) >= now_ms:
            continue        # 这根还在走，收盘价随时会变
        vol = float(k[5])
        recs.append({
            "date": datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).date(),
            "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]), "volume": vol,
            # 币安免费带的两个字段，此前被丢弃：成交额（落 amount）与主动买入占比（喂资金流维）
            "amount": float(k[7]),
            "taker_buy_ratio": round(float(k[9]) / vol, 4) if vol > 0 else None,
        })
    if not recs:
        # 拿到的全是未收盘 bar（盘中增量更新时的常态：今天这根还没走完）
        return pd.DataFrame()
    return pd.DataFrame(recs)[["date", "open", "high", "low", "close", "volume",
                               "amount", "taker_buy_ratio"]]


def klines_df_to_records(symbol: str, df: pd.DataFrame) -> list[dict]:
    """标准 DataFrame → `bulk_upsert_quotes` 认的记录格式（market=crypto）。

    与 `bulk_upsert.yf_df_to_records` 对齐字段；坏行（含 NaN OHLC）逐行跳过，
    不让整批陪葬（同 yf 路径的教训）。symbol 落库保留项目内形态（带 .BN）。
    """
    records: list[dict] = []
    for _, row in df.iterrows():
        o, h, low_, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
        if any(v is None or v != v for v in (o, h, low_, c)):  # v!=v 判 NaN
            continue
        d = row["date"]
        records.append({
            "symbol": symbol, "market": CRYPTO,
            "date": d.isoformat() if hasattr(d, "isoformat") else str(d)[:10],
            "open": float(o), "high": float(h), "low": float(low_), "close": float(c),
            "volume": float(row.get("volume") or 0),
            "amount": float(row["amount"]) if row.get("amount") is not None else None,
            "turnover": None,
        })
    return records
