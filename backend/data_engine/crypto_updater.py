"""加密货币（币安现货）日线**增量**更新 + 情报刷新 —— 7×24 独立链。

## 为什么独立于股票每日链

crypto **7×24 无休市、无收盘时点、无交易日历**，股票链的假设全不适用：
- `DailyUpdater` 硬过滤 `market=='a_share'`、`overseas_daily_updater` 有 Yahoo 锁/frontier
  锚点/周末感知 —— 对 crypto 全是噪声。
- crypto「最新可用日」几乎总是昨天/今天，不需要探 frontier（币安 klines 直接给到今天）。

所以走独立 job（`crypto_scheduler.py`，IntervalTrigger 常转），不挂主链、不用 cron 工作日。

## 两条粒度

- **行情**（全 universe 几百个交易对）：币安 klines 便宜，每次增量只补缺的几天。
- **情报**（仅 focus set 主流币）：CoinGecko/币安衍生品有限速且只对主流有意义，
  不对几百个山寨逐个打。focus set = 策展主流表 ∩ universe。

## 出网合规

行情走 `CryptoFetcher`（acquisition 门面），情报走 `crypto_intel` / `crypto_derivatives`
（同门面）。本模块只编排 + 落库，不直接出网。
"""
import os
import time
from datetime import date, datetime, timedelta, timezone

from loguru import logger

from acquisition.markets.base import MarketDataRequest
from acquisition.markets.crypto import CryptoFetcher, klines_df_to_records
from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes
from data_engine.storage.database import get_session
from data_engine.storage.models import DailyQuote, StockInfo

# 只补最近多少自然日；缺更多的是「首次铺底」的活（首跑会拉满这个窗口）。
_MAX_LOOKBACK_DAYS = int(os.getenv("CRYPTO_DAILY_MAX_LOOKBACK", "30"))
# 每币请求间的礼貌间隔（秒），防币安限速。
_SLEEP = float(os.getenv("CRYPTO_DAILY_SLEEP", "0.1"))
# 只保留这些计价对进 universe（默认只 USDT）。
_QUOTE_WHITELIST = tuple(
    q.strip().upper() for q in os.getenv("CRYPTO_QUOTES", "USDT").split(",") if q.strip()
)

# 全量 klines 每日只跑一次的进程内标记：scheduler 每 tick 新建 CryptoUpdater 实例，
# 实例态不持久，故用模块级（进程重启后当天首个 tick 会重做一次全量，可接受）。
_last_full_sweep: "date | None" = None


class CryptoUpdater:
    """crypto 7×24 增量更新器（行情 + 情报）。"""

    def __init__(self):
        self.fetcher = CryptoFetcher()

    # ── universe ────────────────────────────────────────────────────────
    def _seed_universe(self, session) -> int:
        """StockInfo 里没有 crypto 记录时，从币安交易对清单铺底。返回新增数。"""
        pairs = self.fetcher.get_stock_list(quote_assets=_QUOTE_WHITELIST)
        existing = {r[0] for r in session.query(StockInfo.symbol)
                    .filter(StockInfo.market == "crypto").all()}
        added = 0
        for p in pairs:
            if p["symbol"] in existing:
                continue
            session.add(StockInfo(symbol=p["symbol"], name=p["name"], market="crypto",
                                  exchange="BINANCE", stock_type="crypto", is_active=1))
            added += 1
        if added:
            session.commit()
            logger.info(f"[crypto] universe 铺底新增 {added} 个交易对")
        return added

    def _universe(self, session) -> list[str]:
        """当前 crypto universe（StockInfo market=crypto & stock_type=crypto & 活跃）。空则铺底。"""
        q = (session.query(StockInfo.symbol)
             .filter(StockInfo.market == "crypto",
                     StockInfo.stock_type == "crypto",
                     StockInfo.is_active == 1))
        symbols = [r[0] for r in q.all()]
        if not symbols:
            self._seed_universe(session)
            symbols = [r[0] for r in q.all()]
        return symbols

    def _latest_dates(self, session) -> dict[str, date]:
        """{symbol: 库里最新日} —— crypto 全市场一次查出，供增量算起点。"""
        from sqlalchemy import func
        rows = (session.query(DailyQuote.symbol, func.max(DailyQuote.date))
                .filter(DailyQuote.market == "crypto")
                .group_by(DailyQuote.symbol).all())
        out: dict[str, date] = {}
        for sym, d in rows:
            if isinstance(d, str):
                try:
                    d = datetime.strptime(d, "%Y-%m-%d").date()
                except ValueError:
                    continue
            elif isinstance(d, datetime):
                d = d.date()
            if isinstance(d, date):
                out[sym] = d
        return out

    # ── 行情增量 ─────────────────────────────────────────────────────────
    def update_klines(self, session, symbols: list[str] | None = None) -> dict:
        """全 universe 增量补日线。每币从 max(库里日期) 起补到今天（重叠末日靠幂等覆盖）。"""
        universe = symbols if symbols is not None else self._universe(session)
        latest = self._latest_dates(session)
        today = datetime.now(timezone.utc).date()
        floor = today - timedelta(days=_MAX_LOOKBACK_DAYS)

        updated_syms = 0
        total_rows = 0
        failed = 0
        for sym in universe:
            start = latest.get(sym)
            start = max(start, floor) if start else floor   # 缺得太多的只补窗口，铺底是另一回事
            if start > today:
                continue
            try:
                resp = self.fetcher.fetch_daily(MarketDataRequest(
                    symbol=sym, start_date=start.isoformat(),
                    end_date=today.isoformat(), freq="1d"))
                records = klines_df_to_records(sym, resp.data)
                if records:
                    total_rows += bulk_upsert_quotes(session, records)
                    updated_syms += 1
            except Exception as e:  # noqa: BLE001 — 单币失败不拖垮全体，下轮自然补
                failed += 1
                logger.warning(f"[crypto] {sym} 增量失败: {e}")
            if _SLEEP:
                time.sleep(_SLEEP)
        logger.info(f"[crypto] 行情增量：{updated_syms}/{len(universe)} 币更新，"
                    f"{total_rows} 行，{failed} 失败")
        return {"universe": len(universe), "updated": updated_syms,
                "rows": total_rows, "failed": failed}

    def backfill_klines(self, session, lookback_days: int = 400,
                        symbols: list[str] | None = None) -> dict:
        """**一次性深度回补**：绕开 `update_klines` 的 30 天 floor，把历史拉深。

        为什么单列：日常增量刻意只补最近 `_MAX_LOOKBACK_DAYS`（30）天窗口（省请求），
        导致技术指标/信号/200 日大势都算在薄数据上。本方法从 `today - lookback_days`
        起对每币深拉一次（fetch 分页，>1000 才多请求；400 天单页搞定），幂等覆盖。
        默认 400 天（够 200 日 MA + 缓冲）。跑一次即可，之后靠增量维持。
        """
        universe = symbols if symbols is not None else self._universe(session)
        today = datetime.now(timezone.utc).date()
        start = (today - timedelta(days=lookback_days)).isoformat()
        updated_syms = total_rows = failed = 0
        for sym in universe:
            try:
                resp = self.fetcher.fetch_daily(MarketDataRequest(
                    symbol=sym, start_date=start, end_date=today.isoformat(), freq="1d"))
                records = klines_df_to_records(sym, resp.data)
                if records:
                    total_rows += bulk_upsert_quotes(session, records)
                    updated_syms += 1
            except Exception as e:  # noqa: BLE001 — 单币失败不拖垮全体
                failed += 1
                logger.warning(f"[crypto] {sym} 回补失败: {e}")
            if _SLEEP:
                time.sleep(_SLEEP)
        logger.info(f"[crypto] 深度回补({lookback_days}d)：{updated_syms}/{len(universe)} 币，"
                    f"{total_rows} 行，{failed} 失败")
        return {"universe": len(universe), "updated": updated_syms,
                "rows": total_rows, "failed": failed, "lookback_days": lookback_days}

    # ── 情报刷新（仅 focus set）────────────────────────────────────────
    def _focus_set(self, session) -> list[str]:
        """情报聚焦集 = 策展主流表 ∩ universe（可 .env CRYPTO_FOCUS 覆盖为显式列表）。"""
        override = os.getenv("CRYPTO_FOCUS", "").strip()
        universe = set(self._universe(session))
        if override:
            return [s for s in (x.strip() for x in override.split(",")) if s in universe]
        from crypto_intel_engine.resolver import _CURATED
        want = {f"{base}USDT.BN" for base in _CURATED}
        return [s for s in want if s in universe]

    def refresh_market_context(self, session) -> int:
        """市场级情绪/大势 → CryptoMetric(symbol='MARKET')。返回写入指标数。"""
        from crypto_intel_engine.scorer import market_context
        from crypto_intel_engine.store import upsert_market_metrics
        ctx = market_context()
        today = datetime.now(timezone.utc).date()
        fng = (ctx.get("fear_greed") or {}).get("value")
        metrics = {
            "fear_greed": fng,
            "btc_dominance": ctx.get("btc_dominance"),
            "total_market_cap_usd": ctx.get("total_market_cap_usd"),
        }
        return upsert_market_metrics(session, today, metrics, source="coingecko+alt.me")

    def refresh_intel(self, session, symbols: list[str] | None = None) -> dict:
        """focus set 的排雷快照 → CryptoAsset + 衍生品 → CryptoMetric。"""
        from acquisition.markets import crypto_derivatives as deriv
        from crypto_intel_engine.scorer import screen_coin
        from crypto_intel_engine.store import upsert_asset, upsert_metric

        focus = symbols if symbols is not None else self._focus_set(session)
        today = datetime.now(timezone.utc).date()
        scored = 0
        for sym in focus:
            try:
                r = screen_coin(sym)
                d = r.get("dimensions", {})
                if r.get("coingecko_id"):
                    upsert_asset(session, sym, {
                        "base_asset": r.get("base_asset"),
                        "coingecko_id": r.get("coingecko_id"),
                        "circulating_supply": d.get("circulating_supply"),
                        "max_supply": d.get("max_supply"),
                        "market_cap": d.get("market_cap_usd"),
                        "fdv": d.get("fdv_usd"),
                        "inflation_flag": 1 if d.get("max_supply") is None else 0,
                    })
                    upsert_metric(session, sym, today, "risk_score", r.get("score"), "crypto_intel")
                    scored += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[crypto] 排雷刷新失败 {sym}: {e}")
            try:
                snap = deriv.get_derivatives_snapshot(sym)
                f = (snap.get("funding") or {}).get("funding_rate")
                upsert_metric(session, sym, today, "funding_rate", f, "binance")
                oi = (snap.get("open_interest") or {}).get("oi")
                upsert_metric(session, sym, today, "open_interest", oi, "binance")
                ls = (snap.get("long_short") or {}).get("ratio")
                upsert_metric(session, sym, today, "long_short_ratio", ls, "binance")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[crypto] 衍生品刷新失败 {sym}: {e}")
            if _SLEEP:
                time.sleep(_SLEEP)
        session.commit()
        logger.info(f"[crypto] 情报刷新：{scored}/{len(focus)} focus 币")
        return {"focus": len(focus), "scored": scored}

    # ── 编排 ────────────────────────────────────────────────────────────
    def run(self, with_intel: bool = True) -> dict:
        """一轮完整刷新：行情增量 + 市场情绪 + focus 情报。各段独立 try，一段挂不拖累其它。

        行情两档粒度：常规 tick 只补 **focus 主流子集**（便宜、高频）；**每天首个 tick**
        做一次全 universe 全量（几百对靠这次自然追平）。避免每 30 分钟串行打几百次请求。
        `CRYPTO_KLINE_FULL_EVERY_TICK=true` 回滚为每 tick 全量。
        """
        global _last_full_sweep
        session = get_session()
        summary: dict = {}
        try:
            today = datetime.now(timezone.utc).date()
            force_full = os.getenv("CRYPTO_KLINE_FULL_EVERY_TICK", "false").lower() == "true"
            full = force_full or _last_full_sweep != today
            try:
                syms = None if full else self._focus_set(session)
                summary["klines"] = self.update_klines(session, symbols=syms)
                summary["klines"]["scope"] = "full" if full else "focus"
                if full and not force_full:
                    _last_full_sweep = today   # 全量成功跑完才记账，失败下轮重试
            except Exception as e:  # noqa: BLE001
                logger.error(f"[crypto] 行情增量整体失败: {e}")
                summary["klines"] = {"error": str(e)}
            try:
                summary["market_context"] = self.refresh_market_context(session)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[crypto] 市场情绪刷新失败: {e}")
                summary["market_context"] = {"error": str(e)}
            if with_intel:
                try:
                    summary["intel"] = self.refresh_intel(session)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"[crypto] 情报刷新失败: {e}")
                    summary["intel"] = {"error": str(e)}
        finally:
            session.close()
        return summary
