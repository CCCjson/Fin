"""
全市场日线增量更新器

复用 EastMoneyCrawler + ProxyManager，支持流式进度回调。
收盘后使用 clist 批量接口（~55 次请求，30s）补最新一天数据；
缺历史数据的股票走逐只慢路径。
"""
import os
import sys
import json
import time
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Generator, Optional, Dict, List, Tuple

from loguru import logger
from sqlalchemy import func

from net.proxy_pool import ProxyPool, is_proxy_connect_error

from data_engine.storage.database import get_session, engine
from data_engine.storage.models import StockInfo, DailyQuote, DataUpdateLog
from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes, klines_to_records
from data_engine.liveness import LivenessTracker
from net import ProxyManager

# eastmoney_crawler 还住在 scripts/（13.4-2 迁 acquisition/markets 时这段就没了）
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from eastmoney_crawler import EastMoneyCrawler, CrawlerConfig, parse_kline_data, ProxyTimeoutError, resolve_eastmoney_market  # noqa: E402

# 批量行情接口
from data_engine.fetchers.realtime import fetch_a_share_realtime_cached

# 进程级防双跑锁：慢路径要几分钟到几十分钟，双跑会互相抢代理 IP 和写库
# （2026-07-07 实测过同一分钟内被连点两次，日志里两条"增量更新完成"相差 44ms）
_RUN_LOCK = threading.Lock()


class ProxyServiceUnavailable(RuntimeError):
    """串行慢路径：连续换 IP 全部失败，判定快代理服务不可用，中止本次更新（绝不降级直连）。"""


class DailyUpdater:
    """全市场日线增量更新器"""

    # 每处理多少只后主动换 IP（并发模式下为 worker 本地计数）
    SWITCH_IP_EVERY = 800
    # 慢路径单只最多重排队次数（代理超时换 IP 后重试）
    MAX_REQUEUE = 2
    # 串行模式：连续换 IP 失败达到这个次数，判定快代理服务不可用，中止本次更新
    CONSEC_PROXY_FAIL_LIMIT = 3
    # pytdx 指数成交量单位是百手，东财是手
    PYTDX_INDEX_VOLUME_FACTOR = 100

    def __init__(self):
        self.proxy_mgr = ProxyManager()
        self.crawler: Optional[EastMoneyCrawler] = None
        self.current_proxy = None
        self.proxy_switch_count = 0
        self._consec_proxy_fail = 0

    def _init_crawler(self):
        """初始化爬虫和第一个代理"""
        self._consec_proxy_fail = 0
        self.current_proxy = self.proxy_mgr.fetch_one_proxy()
        use_proxy = self.current_proxy is not None

        self.crawler = EastMoneyCrawler(CrawlerConfig(
            min_delay=0.3 if use_proxy else 2.0,
            max_delay=1.5 if use_proxy else 8.0,
            max_retries=0,
            retry_delay=0,
            timeout=8 if use_proxy else 30,
            rate_limit_pause=30.0 if use_proxy else 90.0,
        ))

        # 用代理重新预热获取 Cookie
        proxies = self.current_proxy.to_requests_proxies() if self.current_proxy else None
        self.crawler._warm_up(proxies=proxies)

        if self.current_proxy:
            logger.info(f"初始代理: {self.current_proxy.ip}:{self.current_proxy.port}")
        else:
            logger.warning("未获取到代理 IP，使用直连模式")

    def _get_proxies(self) -> Optional[Dict[str, str]]:
        """获取当前代理的 requests proxies 参数。

        未配置快代理时返回 None（直连是唯一选项，不算降级）。配置了快代理但
        换不到新 IP 时同样返回 None，调用方（串行慢路径主循环）必须把这轮
        当作"暂时没有可用代理"跳过，绝不能拿 None 直接发请求给东财（会被掐断）。
        """
        if self.current_proxy and not self.current_proxy.is_expired:
            return self.current_proxy.to_requests_proxies()
        # 代理过期，切换
        return self._switch_proxy_or_abort()

    def _switch_proxy_or_abort(self) -> Optional[Dict[str, str]]:
        """切换代理；配置了快代理但连续换 IP 失败达到阈值时判定服务不可用并中止。

        Returns:
            新代理的 requests proxies；未配置快代理，或换到阈值前的单次失败，返回 None。
        Raises:
            ProxyServiceUnavailable: 连续换 IP 失败达到 CONSEC_PROXY_FAIL_LIMIT 次。
        """
        if not self.proxy_mgr.api_url:
            return None  # 压根没配快代理，直连是唯一选项，不是"重试失败后降级"

        self.current_proxy = self.proxy_mgr.switch_proxy()
        self.proxy_switch_count += 1
        if self.current_proxy:
            self._consec_proxy_fail = 0
            logger.info(f"切换到新代理: {self.current_proxy.ip}:{self.current_proxy.port}")
            return self.current_proxy.to_requests_proxies()

        self._consec_proxy_fail += 1
        logger.warning(f"获取新代理失败（连续 {self._consec_proxy_fail}/{self.CONSEC_PROXY_FAIL_LIMIT} 次）")
        if self._consec_proxy_fail >= self.CONSEC_PROXY_FAIL_LIMIT:
            raise ProxyServiceUnavailable(
                f"快代理连续 {self._consec_proxy_fail} 次换 IP 失败，判定服务当前不可用，"
                f"中止本次更新（请检查快代理订单余额 / 网络）"
            )
        return None

    def _switch_proxy_on_error(self):
        """请求失败时切换代理（可能抛出 ProxyServiceUnavailable 中止本次更新）"""
        proxies = self._switch_proxy_or_abort()
        if self.crawler:
            self.crawler.reset_session(proxies=proxies)

    def _resolve_worker_count(self) -> int:
        """慢路径并发 worker 数：DAILY_UPDATE_WORKERS 环境变量控制（1-6，默认 4）。

        DAILY_UPDATE_WORKERS=1 精确复现旧串行行为（回滚开关）；
        未配置快代理时强制 1，绝不并发裸打东财。
        """
        try:
            workers = int(os.getenv("DAILY_UPDATE_WORKERS", "4"))
        except ValueError:
            workers = 4
        workers = max(1, min(workers, 6))
        if not self.proxy_mgr.api_url:
            return 1
        return workers

    def _update_indices_via_pytdx(
        self,
        session,
        index_stocks: List,
        latest_dates: Dict[str, Optional[date]],
        target_date: date,
    ) -> Tuple[int, int, List]:
        """指数走 pytdx 通达信接口更新（免费、无限速、socket 极快）。

        指数无复权问题，pytdx 原始价与东财前复权一致（已实测验证）；
        成交量 pytdx 单位为百手，×100 对齐东财的手。

        Returns:
            (成功只数, 写入记录数, 失败的股票列表 — 回落东财慢路径)
        """
        try:
            from data_engine.fetchers.pytdx_fetcher import PytdxFetcher
        except ImportError as e:
            logger.warning(f"pytdx 不可用，指数转东财慢路径: {e}")
            return 0, 0, list(index_stocks)

        ok_count = 0
        records_written = 0
        failed: List = []

        fetcher = PytdxFetcher()
        try:
            for stock in index_stocks:
                symbol = stock.symbol
                latest = latest_dates.get(symbol)
                if latest:
                    # 缺口天数 + 余量（含盘中过期数据需覆盖的当天）
                    count = min((target_date - latest).days + 5, 800)
                else:
                    count = 800

                df = fetcher.fetch_daily_bars(symbol, count=count, is_index=True)
                if df.empty:
                    failed.append(stock)
                    continue

                df["date"] = df["date"].dt.date
                mask = df["date"] <= target_date
                if latest:
                    mask &= df["date"] >= latest  # 含 latest 当天，覆盖盘中截断数据
                df = df[mask]
                if df.empty:
                    failed.append(stock)
                    continue

                records = [{
                    "symbol": symbol,
                    "market": "a_share",
                    "date": row["date"].isoformat(),
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"] * self.PYTDX_INDEX_VOLUME_FACTOR,
                    "amount": row.get("amount"),
                    "turnover": None,
                } for _, row in df.iterrows()]

                try:
                    records_written += self._bulk_upsert(session, records)
                    ok_count += 1
                except Exception as e:
                    logger.error(f"pytdx 指数 {symbol} 写入失败: {e}")
                    failed.append(stock)
        finally:
            fetcher.close()

        logger.info(f"pytdx 指数更新: 成功 {ok_count}/{len(index_stocks)} 只, "
                    f"写入 {records_written} 条")
        return ok_count, records_written, failed

    # ------------------------------------------------------------------
    # 探测 & 判断
    # ------------------------------------------------------------------

    def _probe_latest_trading_date(self) -> Tuple[Optional[date], Optional[date]]:
        """探测 API 当前最新可用交易日，同时返回上一个交易日。

        Returns:
            (target_date, prev_trading_date)
            - target_date: 最新交易日（用作数据目标日期）
            - prev_trading_date: 倒数第二个交易日（用于判断"只差一天"的股票）
        """
        proxies = self._get_proxies()
        try:
            data = self.crawler.fetch_stock_history(
                "000001", "20260101",
                date.today().strftime("%Y%m%d"),
                proxies=proxies,
            )
            if data and data.get("klines"):
                klines = data["klines"]
                last_kline = klines[-1]
                last_date_str = last_kline.split(",")[0]
                target = datetime.strptime(last_date_str, "%Y-%m-%d").date()

                prev = None
                if len(klines) >= 2:
                    prev_date_str = klines[-2].split(",")[0]
                    prev = datetime.strptime(prev_date_str, "%Y-%m-%d").date()

                return target, prev
        except Exception as e:
            logger.warning(f"探测最新交易日失败: {e}")
        return None, None

    @staticmethod
    def _is_market_closed(target_date: date) -> bool:
        """判断 target_date 的交易是否已收盘（盘中数据不完整，不可用批量接口）。

        - target_date < today → True（历史日期，肯定已收盘）
        - target_date == today 且当前北京时间 > 15:05 → True
        - 其他 → False
        """
        today = date.today()
        if target_date < today:
            return True
        if target_date > today:
            return False
        # target_date == today，检查当前时间（使用本地时间，假设运行在东八区）
        try:
            from zoneinfo import ZoneInfo
            now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
        except Exception:
            # fallback: 假设本地就是东八区
            now_sh = datetime.now()
        return now_sh.hour > 15 or (now_sh.hour == 15 and now_sh.minute >= 5)

    # ------------------------------------------------------------------
    # 批量获取 & 写入
    # ------------------------------------------------------------------

    def _fetch_batch_quotes(self) -> Dict[str, Dict]:
        """调用 clist 批量接口获取全市场最新日行情。

        Returns:
            {symbol: {"open": ..., "high": ..., "low": ..., "close": ...,
                      "volume": ..., "amount": ..., "turnover": ...}, ...}
        """
        # 不传单代理：让 fetch 层走多 IP 并发翻页（收盘后数据已定型，强制绕过缓存）
        raw_list = fetch_a_share_realtime_cached(force=True)

        result: Dict[str, Dict] = {}
        for item in raw_list:
            price = item.get("price")
            if price is None or price == 0:
                continue  # 停牌股跳过
            symbol = item.get("symbol")
            if not symbol:
                continue
            result[symbol] = {
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": price,
                "volume": item.get("volume"),
                "amount": item.get("amount"),
                "turnover": item.get("turnover"),
            }
        logger.info(f"批量接口获取到 {len(result)} 只有效行情")
        return result

    @staticmethod
    def _bulk_upsert(session, records: List[Dict]) -> int:
        """用 INSERT OR REPLACE 批量写入日线数据（委托给 deep_history.bulk_upsert 共用实现）"""
        return bulk_upsert_quotes(session, records)

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def update_stream(self) -> Generator[str, None, None]:
        """流式增量更新全市场日线数据（进程级防双跑锁包装，实现见 `_update_stream_impl`）"""
        if not _RUN_LOCK.acquire(blocking=False):
            yield json.dumps(
                {"event": "error", "message": "日线更新已在运行中，请等待当前任务结束后重试"},
                ensure_ascii=False,
            ) + "\n"
            return
        try:
            yield from self._update_stream_impl()
        finally:
            _RUN_LOCK.release()

    def _update_stream_impl(self) -> Generator[str, None, None]:
        """
        流式增量更新全市场日线数据

        Yields:
            NDJSON 事件字符串:
            - {"event": "start", "total": N}
            - {"event": "progress", "current": i, "total": N, "symbol": "...", ...}
            - {"event": "complete", "success": N, "skipped": N, "failed": N, ...}
        """
        start_time = datetime.now()
        session = get_session()

        # 获取所有活跃 A 股。ETF 不再获取（2026-07-09 Jason 拍板：不交易 ETF，
        # 1393 只 ETF 已全部 is_active=0；这里再排一道 stock_type，防止将来
        # 列表重导入误激活后又被拉回来）
        stocks = session.query(StockInfo).filter(
            StockInfo.market == "a_share",
            StockInfo.is_active == 1,
            StockInfo.stock_type != "etf",
        ).all()

        if not stocks:
            yield json.dumps({"event": "error", "message": "股票列表为空，请先运行全量导入脚本"}, ensure_ascii=False) + "\n"
            session.close()
            return

        total = len(stocks)

        # 预查每只股票的 stock_type 和 exchange，用于后续传 secid_market
        stock_meta: Dict[str, Dict] = {}
        for s in stocks:
            stock_meta[s.symbol] = {
                "stock_type": s.stock_type or "stock",
                "exchange": s.exchange,
            }

        # 初始化爬虫（探测需要用到）
        self._init_crawler()

        # 探测 API 最新可用交易日 + 上一个交易日
        target_date, prev_trading_date = self._probe_latest_trading_date()
        if target_date:
            logger.info(f"探测到最新交易日: {target_date}, 上一交易日: {prev_trading_date}")
        else:
            target_date = date.today()
            logger.warning(f"探测失败，使用今天 {target_date} 作为目标日期")
        target_date_str = target_date.strftime("%Y%m%d")

        # 预查每只股票在 DB 中的最新日期
        latest_dates: Dict[str, Optional[date]] = {}
        latest_rows = session.query(
            DailyQuote.symbol,
            func.max(DailyQuote.date),
        ).filter(
            DailyQuote.symbol.in_([s.symbol for s in stocks])
        ).group_by(DailyQuote.symbol).all()
        for sym, d in latest_rows:
            latest_dates[sym] = d

        # 判断是否可用批量路径：只要有 prev_trading_date 就行
        # 盘中数据虽不完整，但慢路径拿到的也一样不完整，批量至少更快
        use_batch = prev_trading_date is not None

        # 检测"盘中写入的过期数据"：
        # 如果 target_date == 今天 且 现在已收盘，DB 里 date==today 但 updated_at < 15:00 的
        # 属于盘中不完整数据，需要用收盘后的完整数据覆盖
        needs_refresh: set = set()
        market_closed = self._is_market_closed(target_date)
        if target_date == date.today() and market_closed:
            try:
                from zoneinfo import ZoneInfo
                cutoff = datetime.combine(target_date, datetime.min.time().replace(hour=15, minute=0),
                                          tzinfo=ZoneInfo("Asia/Shanghai"))
                # updated_at 存的是本地时间（无时区），直接比较 naive
                cutoff_naive = cutoff.replace(tzinfo=None)
            except Exception:
                cutoff_naive = datetime.combine(target_date, datetime.min.time().replace(hour=15, minute=0))

            stale_rows = session.query(DailyQuote.symbol).filter(
                DailyQuote.date == target_date,
                DailyQuote.updated_at < cutoff_naive,
            ).all()
            needs_refresh = {row[0] for row in stale_rows}
            if needs_refresh:
                logger.info(f"检测到 {len(needs_refresh)} 只股票有盘中过期数据，将重新获取")

        # 分类股票
        already_fresh_list: List[StockInfo] = []
        batch_eligible: List[StockInfo] = []
        history_needed: List[StockInfo] = []

        for stock in stocks:
            latest = latest_dates.get(stock.symbol)
            meta = stock_meta.get(stock.symbol, {})
            stype = meta.get("stock_type", "stock")

            if latest and latest >= target_date and stock.symbol not in needs_refresh:
                already_fresh_list.append(stock)
            elif use_batch and stype == "stock" and latest and (latest == prev_trading_date or stock.symbol in needs_refresh):
                # 只差一天 或 盘中过期数据 → 批量路径（仅股票，ETF/指数走慢路径）
                batch_eligible.append(stock)
            else:
                history_needed.append(stock)

        already_fresh = len(already_fresh_list)
        total_need_update = len(batch_eligible) + len(history_needed)

        logger.info(
            f"全市场 {total} 只，已最新 {already_fresh} 只，"
            f"批量路径 {len(batch_eligible)} 只，慢路径 {len(history_needed)} 只"
            f"（目标日: {target_date}, 批量模式: {use_batch}）"
        )

        yield json.dumps({
            "event": "start",
            "total": total_need_update,
            "skipped_fresh": already_fresh,
            "date": str(target_date),
            "batch_mode": use_batch,
            "batch_count": len(batch_eligible),
            "history_count": len(history_needed),
        }, ensure_ascii=False) + "\n"

        if total_need_update == 0:
            yield json.dumps({
                "event": "complete",
                "success": 0,
                "skipped": already_fresh,
                "failed": 0,
                "new_records": 0,
                "proxy_switches": 0,
                "duration_seconds": 0,
            }, ensure_ascii=False) + "\n"
            session.close()
            return

        success_count = 0
        fail_count = 0
        total_records = 0
        progress_idx = 0  # 全局进度计数
        backfilled: List[Dict] = []  # 慢路径里补了不止 1 天的股票（真正意义上的"补齐"）
        abort_reason: Optional[str] = None  # 慢路径中途中止的原因（熔断/静默超时等）

        # =============================================
        # 阶段 1: 批量路径
        # =============================================
        if batch_eligible:
            yield json.dumps({
                "event": "progress",
                "current": 0,
                "total": total_need_update,
                "symbol": "",
                "name": "正在批量获取全市场行情...",
                "success": 0,
                "failed": 0,
                "new_records": 0,
                "phase": "batch_fetch",
            }, ensure_ascii=False) + "\n"

            try:
                batch_quotes = self._fetch_batch_quotes()
            except Exception as e:
                logger.warning(f"批量接口调用失败，全部转入慢路径: {e}")
                batch_quotes = {}

            # 构造 INSERT OR REPLACE 记录
            batch_records: List[Dict] = []
            batch_missing: List[StockInfo] = []  # 批量接口中没有的股票 → 转慢路径

            for stock in batch_eligible:
                quote = batch_quotes.get(stock.symbol)
                if quote and quote.get("close") and quote["close"] > 0:
                    batch_records.append({
                        "symbol": stock.symbol,
                        "market": "a_share",
                        "date": target_date.isoformat(),
                        "open": quote["open"],
                        "high": quote["high"],
                        "low": quote["low"],
                        "close": quote["close"],
                        "volume": quote["volume"],
                        "amount": quote.get("amount"),
                        "turnover": quote.get("turnover"),
                    })
                else:
                    batch_missing.append(stock)

            # 批量写入
            if batch_records:
                try:
                    upserted = self._bulk_upsert(session, batch_records)
                    success_count += len(batch_records)
                    total_records += upserted
                    logger.info(f"批量写入 {upserted} 条日线记录")
                except Exception as e:
                    logger.error(f"批量写入失败: {e}")
                    # 写入失败的全部转入慢路径
                    batch_missing.extend(batch_eligible)
                    batch_records.clear()

            # 没有批量数据的（停牌等）转入慢路径
            if batch_missing:
                logger.info(f"{len(batch_missing)} 只股票批量接口无数据，转入慢路径")
                history_needed.extend(batch_missing)

            progress_idx = len(batch_eligible)
            yield json.dumps({
                "event": "progress",
                "current": progress_idx,
                "total": total_need_update,
                "symbol": "",
                "name": f"批量写入完成 ({len(batch_records)} 只)",
                "success": success_count,
                "failed": fail_count,
                "new_records": total_records,
                "phase": "batch_done",
            }, ensure_ascii=False) + "\n"

        # =============================================
        # 阶段 2a: 指数优先走 pytdx（免限速，东财失败的回落慢路径）
        # =============================================
        index_stocks = [
            s for s in history_needed
            if stock_meta.get(s.symbol, {}).get("stock_type") == "index"
        ]
        if index_stocks:
            idx_ok, idx_records, idx_failed = self._update_indices_via_pytdx(
                session, index_stocks, latest_dates, target_date,
            )
            success_count += idx_ok
            total_records += idx_records
            progress_idx += idx_ok

            idx_failed_syms = {s.symbol for s in idx_failed}
            history_needed = [
                s for s in history_needed
                if stock_meta.get(s.symbol, {}).get("stock_type") != "index"
                or s.symbol in idx_failed_syms
            ]

            yield json.dumps({
                "event": "progress",
                "current": progress_idx,
                "total": total_need_update,
                "symbol": "",
                "name": f"指数已通过 pytdx 更新 ({idx_ok}/{len(index_stocks)})",
                "success": success_count,
                "failed": fail_count,
                "new_records": total_records,
                "phase": "history",
            }, ensure_ascii=False) + "\n"

        # =============================================
        # 阶段 2b: 慢路径（逐只请求历史 K 线）
        # =============================================
        workers = self._resolve_worker_count()

        if history_needed and workers <= 1:
            # ---- 串行模式（DAILY_UPDATE_WORKERS=1 或无代理，与旧行为一致）----
            emit_interval = max(1, len(history_needed) // 200)
            tracker = LivenessTracker(heartbeat_interval=2.0)
            serial_abort: Optional[str] = None
            last_i = 0

            try:
                for i, stock in enumerate(history_needed):
                    last_i = i
                    symbol = stock.symbol
                    code = symbol.split(".")[0]
                    latest = latest_dates.get(symbol)

                    # 增量起始日期
                    if latest:
                        fetch_start = (latest + timedelta(days=1)).strftime("%Y%m%d")
                    else:
                        fetch_start = "20100101"

                    # 跳过如果起始日期 > 目标日期
                    if fetch_start > target_date_str:
                        success_count += 1
                        progress_idx += 1
                        continue

                    proxies = self._get_proxies()
                    if proxies is None and self.proxy_mgr.api_url:
                        # 配置了快代理但本轮暂时换不到 IP：跳过本只，绝不发直连请求
                        fail_count += 1
                        progress_idx += 1
                        continue

                    # 指数/ETF 需要传 secid_market 避免 secid 判断错误
                    meta = stock_meta.get(symbol, {})
                    stype = meta.get("stock_type", "stock")
                    secid_mkt = None
                    if stype in ("index", "etf"):
                        exchange = meta.get("exchange")
                        if exchange == "SH":
                            secid_mkt = 1
                        elif exchange in ("SZ", "BJ"):
                            secid_mkt = 0

                    try:
                        data = self.crawler.fetch_stock_history(
                            code, fetch_start, target_date_str,
                            proxies=proxies, secid_market=secid_mkt,
                        )

                        saved = 0
                        if data:
                            klines = parse_kline_data(data)
                            if klines:
                                saved = self._save_klines(session, symbol, klines)
                                total_records += saved
                                if saved > 1:
                                    backfilled.append({"symbol": symbol, "name": stock.name, "days": saved})

                        success_count += 1

                    except ProxyTimeoutError:
                        fail_count += 1
                        self._switch_proxy_on_error()

                    except Exception as e:
                        fail_count += 1
                        logger.warning(f"{symbol} 更新失败: {e}")

                    # 定期换 IP（不再 sleep）
                    slow_success = success_count - len(batch_eligible) if batch_eligible else success_count
                    if slow_success > 0 and slow_success % self.SWITCH_IP_EVERY == 0:
                        self._switch_proxy_on_error()

                    progress_idx += 1
                    tracker.touch()

                    # 发送进度（条数节流 + 时间驱动心跳兜底，避免代理抖动时长时间静默）
                    if (i + 1) % emit_interval == 0 or i + 1 == len(history_needed) or tracker.should_heartbeat():
                        yield json.dumps({
                            "event": "progress",
                            "current": progress_idx,
                            "total": total_need_update,
                            "symbol": symbol,
                            "name": stock.name,
                            "success": success_count,
                            "failed": fail_count,
                            "new_records": total_records,
                            "phase": "history",
                        }, ensure_ascii=False) + "\n"
                        tracker.mark_yield()
            except ProxyServiceUnavailable as e:
                logger.error(f"串行慢路径中止: {e}")
                missing = len(history_needed) - last_i
                fail_count += missing
                progress_idx += missing
                serial_abort = "proxy_pool_dead"

            if serial_abort:
                abort_reason = serial_abort
                yield json.dumps({
                    "event": "progress",
                    "current": progress_idx,
                    "total": total_need_update,
                    "symbol": "",
                    "name": "代理与直连均不可用，已中止本次更新",
                    "success": success_count,
                    "failed": fail_count,
                    "new_records": total_records,
                    "phase": "history",
                }, ensure_ascii=False) + "\n"

        elif history_needed:
            # ---- 并发模式：N 个 worker 各持独立代理 IP + 独立限速器 ----
            # 每 IP 请求节奏与旧串行版相同（0.3-1.5s 自适应），东财视角上
            # 是 N 个独立客户端；worker 不碰 session，结果经队列由主线程单写。
            stop_event = threading.Event()
            work_q: "queue.Queue" = queue.Queue()
            result_q: "queue.Queue" = queue.Queue()

            # 注意：只入队纯数据元组，绝不把 ORM 对象带进 worker 线程 ——
            # 主线程 commit 后 session 会 expire 所有对象，worker 再访问属性
            # 会跨线程触发 lazy refresh（session 非线程安全，必挂）
            enqueued = 0
            for stock in history_needed:
                latest = latest_dates.get(stock.symbol)
                if latest:
                    fetch_start = (latest + timedelta(days=1)).strftime("%Y%m%d")
                else:
                    fetch_start = "20100101"
                if fetch_start > target_date_str:
                    success_count += 1
                    progress_idx += 1
                    continue
                work_q.put((stock.symbol, stock.name, fetch_start, 0))
                enqueued += 1

            # 预置批量路径已成功的数量：批量路径不走本池，若不预置，慢路径开局
            # 恰好撞上一批连不上的 ETF 时会误判「快代理整体挂了」而全局中止。
            # 预置后本池一开始就知道快代理是通的，那批 ETF 只各自计失败、任务
            # 继续跑完（Jason 拍板：ETF 不重要，宁可跑完）。
            pool = ProxyPool(size=workers, mgr=self.proxy_mgr,
                             min_delay=0.3, max_delay=1.5,
                             initial_success=success_count)

            def _worker():
                crawler = EastMoneyCrawler(CrawlerConfig(
                    min_delay=0.3, max_delay=1.5, max_retries=0,
                    retry_delay=0, timeout=8, rate_limit_pause=30.0,
                ))
                slot = pool.acquire()
                crawler._warm_up(proxies=slot.to_requests_proxies())
                on_ip = 0

                def _rotate():
                    nonlocal on_ip
                    try:
                        pool.refresh(slot)
                        crawler.reset_session(proxies=slot.to_requests_proxies())
                    except Exception as rot_err:
                        logger.warning(f"换 IP 失败（继续用当前会话）: {rot_err}")
                    on_ip = 0

                try:
                    while not stop_event.is_set():
                        try:
                            symbol, stock_name, fetch_start, retries = work_q.get(timeout=0.5)
                        except queue.Empty:
                            continue

                        try:
                            if slot.proxy is not None and slot.proxy.is_expired:
                                _rotate()

                            meta = stock_meta.get(symbol, {})
                            secid_mkt = None
                            if meta.get("stock_type", "stock") in ("index", "etf"):
                                exchange = meta.get("exchange")
                                if exchange == "SH":
                                    secid_mkt = 1
                                elif exchange in ("SZ", "BJ"):
                                    secid_mkt = 0

                            data = crawler.fetch_stock_history(
                                symbol.split(".")[0], fetch_start, target_date_str,
                                proxies=slot.to_requests_proxies(),
                                secid_market=secid_mkt,
                            )
                            klines = parse_kline_data(data) if data else []
                            pool.report_success(slot)
                            result_q.put({"symbol": symbol, "name": stock_name,
                                          "ok": True, "klines": klines})
                            on_ip += 1
                            if on_ip >= self.SWITCH_IP_EVERY:
                                _rotate()

                        except ProxyTimeoutError as e:
                            proxy_connect = is_proxy_connect_error(e)
                            pool.report_failure(slot, proxy_connect=proxy_connect)
                            _rotate()
                            if retries < self.MAX_REQUEUE:
                                work_q.put((symbol, stock_name, fetch_start, retries + 1))
                                # 重试不是终态，但要让主循环知道 worker 还活着，
                                # 否则代理故障时全队重试、result_q 长时间零终态
                                # 产出会被误判为卡死
                                result_q.put({"kind": "activity", "symbol": symbol,
                                              "name": stock_name,
                                              "reason": "proxy_retry" if proxy_connect else "rate_limited"})
                            else:
                                result_q.put({"symbol": symbol, "name": stock_name,
                                              "ok": False, "klines": []})

                        except Exception as e:
                            logger.warning(f"{symbol} 更新失败: {e}")
                            result_q.put({"symbol": symbol, "name": stock_name,
                                          "ok": False, "klines": []})
                except Exception as e:
                    # worker 意外死亡必须留痕：主循环靠 futures done + 队列判空兜底退出
                    logger.error(f"慢路径 worker 异常退出: {e}")
                    raise
                finally:
                    pool.release(slot)

            executor = ThreadPoolExecutor(max_workers=workers,
                                          thread_name_prefix="daily-slow")
            futures = [executor.submit(_worker) for _ in range(workers)]
            logger.info(f"慢路径并发启动: {workers} workers, {enqueued} 只待更新")

            emit_interval = max(1, enqueued // 200)
            pending_records: List[Dict] = []
            processed = 0
            last_flush = time.time()

            tracker = LivenessTracker(stall_timeout=180.0, heartbeat_interval=2.0)
            retries_total = 0
            last_symbol, last_name = "", ""

            def _progress_event(note: Optional[str] = None, heartbeat: bool = False) -> str:
                payload = {
                    "event": "progress",
                    "current": progress_idx,
                    "total": total_need_update,
                    "symbol": last_symbol,
                    "name": last_name,
                    "success": success_count,
                    "failed": fail_count,
                    "new_records": total_records,
                    "phase": "history",
                    "retries": retries_total,
                    "proxy_state": pool.breaker_state,
                }
                if note:
                    payload["note"] = note
                if heartbeat:
                    payload["heartbeat"] = True
                return json.dumps(payload, ensure_ascii=False) + "\n"

            try:
                while processed < enqueued:
                    # 熔断彻底不可用（直连也连败）时立即中止，不等队列空闲——
                    # 否则代理故障下满队列的重试活动会让下面 queue.Empty 分支
                    # 长期不触发，白白耗时把所有股票挨个跑到 3 次重试耗尽
                    if pool.breaker_state == "dead":
                        missing = enqueued - processed
                        logger.error(f"代理池与直连均不可用，中止本次更新，{missing} 只计为失败")
                        fail_count += missing
                        progress_idx += missing
                        abort_reason = "proxy_pool_dead"
                        yield _progress_event(note="代理与直连均不可用，已中止本次更新")
                        break

                    try:
                        res = result_q.get(timeout=0.5)
                    except queue.Empty:
                        res = None

                    if res is not None:
                        tracker.touch()
                        if res.get("kind") == "activity":
                            retries_total += 1
                            last_symbol, last_name = res["symbol"], res["name"]
                            if tracker.should_heartbeat():
                                yield _progress_event(note=f"{last_symbol} 重试中…")
                                tracker.mark_yield()
                            continue

                    if res is None:
                        if all(f.done() for f in futures) and result_q.empty():
                            missing = enqueued - processed
                            logger.warning(f"慢路径 worker 已全部退出，{missing} 只未返回，计为失败")
                            fail_count += missing
                            progress_idx += missing
                            abort_reason = "workers_exited"
                            break
                        if tracker.is_stalled():
                            missing = enqueued - processed
                            logger.error(f"慢路径 {tracker.stall_timeout:.0f}s 无任何 worker 活动，"
                                         f"中止并将 {missing} 只计为失败")
                            fail_count += missing
                            progress_idx += missing
                            abort_reason = "stalled"
                            yield _progress_event(note=f"{tracker.stall_timeout:.0f}s 无响应，已中止本次更新")
                            break
                        if tracker.should_heartbeat():
                            yield _progress_event(heartbeat=True)
                            tracker.mark_yield()
                        continue

                    processed += 1
                    progress_idx += 1
                    last_symbol, last_name = res["symbol"], res["name"]
                    if res["ok"]:
                        success_count += 1
                        if res["klines"]:
                            pending_records.extend(
                                self._klines_to_records(res["symbol"], res["klines"])
                            )
                            if len(res["klines"]) > 1:
                                backfilled.append({
                                    "symbol": res["symbol"],
                                    "name": res["name"],
                                    "days": len(res["klines"]),
                                })
                    else:
                        fail_count += 1

                    # 批量落库：每 200 只或 2s
                    now = time.time()
                    if pending_records and (
                        processed % 200 == 0
                        or now - last_flush > 2.0
                        or processed == enqueued
                    ):
                        try:
                            total_records += self._bulk_upsert(session, pending_records)
                        except Exception as e:
                            logger.error(f"慢路径批量写入失败（丢弃 {len(pending_records)} 条）: {e}")
                            try:
                                session.rollback()
                            except Exception:
                                pass
                        pending_records = []
                        last_flush = now

                    # 发送进度（条数节流 + 时间驱动心跳兜底）
                    if processed % emit_interval == 0 or processed == enqueued or tracker.should_heartbeat():
                        yield _progress_event()
                        tracker.mark_yield()
            finally:
                stop_event.set()
                if abort_reason:
                    # 清空待处理队列，让还在 get(timeout=0.5) 的 worker 尽快看到
                    # stop_event 退出，避免残留任务继续占线程
                    try:
                        while True:
                            work_q.get_nowait()
                    except queue.Empty:
                        pass
                executor.shutdown(wait=False)

            # 收尾：残余缓冲落库
            if pending_records:
                try:
                    total_records += self._bulk_upsert(session, pending_records)
                except Exception as e:
                    logger.error(f"慢路径批量写入失败（丢弃 {len(pending_records)} 条）: {e}")
                    try:
                        session.rollback()
                    except Exception:
                        pass

            self.proxy_switch_count += pool.stats["ip_fetches"]

        # =============================================
        # 记录日志 & 完成
        # =============================================
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        try:
            log = DataUpdateLog(
                market="a_share",
                update_type="daily_incremental",
                symbols_count=success_count,
                records_count=total_records,
                status="aborted" if abort_reason else ("success" if fail_count == 0 else "partial"),
                started_at=start_time,
                completed_at=end_time,
                duration_seconds=duration,
            )
            session.add(log)
            session.commit()
        except Exception as e:
            logger.warning(f"保存更新日志失败: {e}")

        session.close()

        backfilled.sort(key=lambda r: r["days"], reverse=True)
        BACKFILL_LIST_CAP = 50

        yield json.dumps({
            "event": "complete",
            "success": success_count,
            "skipped": already_fresh,
            "failed": fail_count,
            "new_records": total_records,
            "backfilled_count": len(backfilled),
            "backfilled_stocks": backfilled[:BACKFILL_LIST_CAP],
            "proxy_switches": self.proxy_switch_count,
            "duration_seconds": round(duration, 1),
            "aborted": bool(abort_reason),
            "abort_reason": abort_reason,
        }, ensure_ascii=False) + "\n"

        logger.info(f"增量更新完成: 成功 {success_count}, 跳过 {already_fresh}, "
                     f"失败 {fail_count}, 新记录 {total_records}, 耗时 {duration:.1f}s"
                     + (f"，中止原因: {abort_reason}" if abort_reason else ""))

    @staticmethod
    def _klines_to_records(symbol: str, klines: List[Dict]) -> List[Dict]:
        """K 线 dict 列表转为 _bulk_upsert 所需的记录格式（委托给共用实现）"""
        return klines_to_records(symbol, "a_share", klines)

    def _save_klines(self, session, symbol: str, klines: List[Dict]) -> int:
        """保存 K 线数据到 DB（INSERT OR REPLACE，无需逐行 SELECT）"""
        if not klines:
            return 0
        return self._bulk_upsert(session, self._klines_to_records(symbol, klines))

    def get_update_status(self) -> Dict:
        """查询当前数据覆盖状态"""
        session = get_session()

        total_stocks = session.query(func.count(StockInfo.symbol)).filter(
            StockInfo.market == "a_share",
            StockInfo.is_active == 1,
        ).scalar() or 0

        # 本卡片统计口径是 A股（EastMoneyCrawler 只管 A股，total_stocks 也过滤了
        # market=="a_share"）——这两个查询之前漏了同样的市场过滤，导致 latest_date
        # 可能被港美股的日期带偏、fresh_count 把三个市场的股票数加在一起当分子，
        # 分母却只有 A股 → coverage_pct 冲到 300%+（曾实测 19,871/6,594=301.3%，
        # 拆开正是 a_share 6,567 + hk_stock 2,668 + us_stock 10,636 = 19,871）。
        latest_date_row = session.query(func.max(DailyQuote.date)).filter(
            DailyQuote.market == "a_share",
        ).first()
        latest_date = latest_date_row[0] if latest_date_row and latest_date_row[0] else None

        # 有多少只股票的数据更新到了最新日期
        fresh_count = 0
        if latest_date:
            fresh_count = session.query(func.count(func.distinct(DailyQuote.symbol))).filter(
                DailyQuote.date == latest_date,
                DailyQuote.market == "a_share",
            ).scalar() or 0

        # 上次更新日志
        last_log = session.query(DataUpdateLog).filter(
            DataUpdateLog.update_type.in_(["daily", "daily_incremental"])
        ).order_by(DataUpdateLog.completed_at.desc()).first()

        session.close()

        return {
            "total_stocks": total_stocks,
            "latest_date": str(latest_date) if latest_date else None,
            "stocks_at_latest": fresh_count,
            "coverage_pct": round(fresh_count / total_stocks * 100, 1) if total_stocks > 0 else 0,
            "last_update": {
                "status": last_log.status if last_log else None,
                "records": last_log.records_count if last_log else None,
                "duration": last_log.duration_seconds if last_log else None,
                "completed_at": last_log.completed_at.isoformat() if last_log and last_log.completed_at else None,
            } if last_log else None,
        }
