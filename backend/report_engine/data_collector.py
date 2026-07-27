"""
报告数据采集器 — 从各数据源收集报告所需数据

对外只有 `collect_{market,news,positions,strategy,picks}()` 五个分片入口，一一对应
五个章节 subagent。想只看板块轮动就只采市场那片，不必把整套跑一遍。

分片之间共享 `collect_common()`（行情总览 + 持仓），它是全流程最贵的一步——一次
`market_overview.collect_market_overview()` 出网 + 逐持仓抓新闻，且 `_section_market_context`
让五个章节全都要用它。故加进程内 TTL 缓存，否则 MoneyBill 连调四个章节工具会出网抓四次指数。
"""
import copy
import json
import threading
import time
import pandas as pd
from datetime import date, timedelta, datetime
from typing import Dict, List, Optional, Tuple
from loguru import logger

from common.market import A_SHARE
from common.market_time import market_day_bounds, market_today
from sqlalchemy import func, desc, asc

from common.limit_rules import is_limit_down, is_limit_up
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    Signal, BacktestTask, BacktestResult,
    Order, Trade, DailyQuote, RealtimeSnapshot, StockInfo,
    ManualTrade, UserSettings,
)
from report_engine import picks_log
from report_engine.scorer import SignalScorer
from report_engine.stock_analyzer import StockAnalyzer
from portfolio.calculator import PortfolioCalculator
from analysis_engine import AnalysisEngine


_COMMON_CACHE_TTL_SECONDS = 300
_COMMON_CACHE_MAX_ENTRIES = 4
_common_cache: Dict[tuple, tuple] = {}          # key -> (expires_at, data)
_common_cache_lock = threading.Lock()


def _period_bounds(report_type: str, period_end: Optional[date]) -> tuple:
    """(period_start, period_end, is_weekend) —— 报告期窗口的唯一推导处。"""
    if period_end is None:
        period_end = market_today(A_SHARE)
    days = {"daily": 1, "weekly": 7, "monthly": 30}.get(report_type, 7)
    period_start = period_end - timedelta(days=days)
    is_weekend = period_end.weekday() >= 5   # 周六=5，周日=6
    return period_start, period_end, is_weekend


class ReportDataCollector:
    """收集AI报告所需的各类数据"""

    # ==================================================================
    # 共享片：行情总览 + 持仓（最贵，五个章节全要，故 TTL 缓存）
    # ==================================================================
    def collect_common(self, report_type: str = "weekly", period_end: Optional[date] = None,
                       enable_web_search: bool = False) -> Dict:
        """报告期头部 + market_overview + portfolio（含逐持仓新闻）。

        缓存 key 必须含 report_type 与 period_end——只按时间做 key 会让周报命中日报的
        窗口数据。返回深拷贝，调用方尽管改。
        """
        period_start, period_end, is_weekend = _period_bounds(report_type, period_end)
        key = (report_type, period_end.isoformat(), enable_web_search)

        now = time.monotonic()
        with _common_cache_lock:
            hit = _common_cache.get(key)
            if hit and hit[0] > now:
                return copy.deepcopy(hit[1])

        data: Dict = {
            "report_type": report_type,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "is_weekend": is_weekend,
        }
        session = get_session()
        try:
            data["market_overview"] = self._collect_market_overview(
                session, period_start, period_end, enable_web_search=enable_web_search)
            data["portfolio"] = self._collect_portfolio(session)
            self._attach_position_news(data["portfolio"])
        finally:
            session.close()

        with _common_cache_lock:
            if len(_common_cache) >= _COMMON_CACHE_MAX_ENTRIES:
                oldest = min(_common_cache, key=lambda k: _common_cache[k][0])
                _common_cache.pop(oldest, None)
            _common_cache[key] = (now + _COMMON_CACHE_TTL_SECONDS, copy.deepcopy(data))
        return data

    @staticmethod
    def _attach_position_news(portfolio: Dict) -> None:
        """为持仓股抓取新闻（每只最多2条最重要的，内容300字），就地写进 pos["news"]。"""
        try:
            from news_engine.fetcher import NewsFetcher
            fetcher = NewsFetcher()
        except Exception as e:
            logger.warning(f"持仓新闻抓取失败: {e}")
            return
        for pos in portfolio.get("positions", []):
            try:
                news_list = fetcher.fetch_a_share_news(pos["symbol"])
                pos["news"] = [
                    {
                        "title": n["title"],
                        "content": (n.get("content") or "")[:300],
                        "source": n.get("source", ""),
                        "published_at": str(n.get("published_at", "")),
                    }
                    for n in news_list[:2]
                ]
            except Exception as e:
                logger.warning(f"持仓股 {pos.get('symbol')} 新闻抓取失败，置空继续: {e}")
                pos["news"] = []

    @staticmethod
    def _held_symbols(data: Dict) -> set:
        return {p["symbol"] for p in data.get("portfolio", {}).get("positions", []) if p.get("symbol")}

    # ==================================================================
    # 分片采集：每个章节 subagent 只采自己那块
    # ==================================================================
    def collect_market(self, report_type: str = "weekly", period_end: Optional[date] = None,
                       enable_web_search: bool = False) -> Dict:
        """Ch2 市场总览 + Ch4 板块/北向。只需 common。"""
        return self.collect_common(report_type, period_end, enable_web_search)

    def collect_news(self, report_type: str = "weekly", period_end: Optional[date] = None,
                     enable_web_search: bool = False) -> Dict:
        """Ch3 新闻舆情研判。"""
        data = self.collect_common(report_type, period_end, enable_web_search)
        data["news_analysis"] = self._analyze_news_sentiment(data)
        return data

    def collect_positions(self, report_type: str = "weekly", period_end: Optional[date] = None,
                          enable_web_search: bool = False) -> Dict:
        """Ch5 持仓诊断。要持仓个股信号 + 命中持仓的卖出预警（买入推荐那半边用不上）。"""
        data = self.collect_common(report_type, period_end, enable_web_search)
        period_start = date.fromisoformat(data["period_start"])
        period_end_d = date.fromisoformat(data["period_end"])
        held = self._held_symbols(data)
        session = get_session()
        try:
            data["signals"] = self._collect_signals(session, period_start, period_end_d, held)
            data["top_stocks"] = self._collect_top_stocks(
                session, period_start, period_end_d, held, want=("sell",))
        finally:
            session.close()
        return data

    def collect_strategy(self, report_type: str = "weekly", period_end: Optional[date] = None,
                         enable_web_search: bool = False) -> Dict:
        """Ch6 上期回顾 + Ch7 信号与策略表现。"""
        data = self.collect_common(report_type, period_end, enable_web_search)
        period_start = date.fromisoformat(data["period_start"])
        period_end_d = date.fromisoformat(data["period_end"])
        held = self._held_symbols(data)
        session = get_session()
        try:
            data["previous_report"] = self._collect_previous_recommendations(
                session, period_end_d, report_type)
            data["signals"] = self._collect_signals(session, period_start, period_end_d, held)
            data["backtest"] = self._collect_backtest(session)
        finally:
            session.close()
        return data

    def collect_picks(self, report_type: str = "weekly", period_end: Optional[date] = None,
                      enable_web_search: bool = False) -> Dict:
        """Ch8 买入推荐。"""
        data = self.collect_common(report_type, period_end, enable_web_search)
        period_start = date.fromisoformat(data["period_start"])
        period_end_d = date.fromisoformat(data["period_end"])
        held = self._held_symbols(data)
        session = get_session()
        try:
            data["top_stocks"] = self._collect_top_stocks(
                session, period_start, period_end_d, held, want=("buy",))
        finally:
            session.close()
        return data

    # ------------------------------------------------------------------
    def _collect_market_overview(self, session, period_start: date, period_end: date,
                                 enable_web_search: bool = False) -> Dict:
        """收集市场总览数据，始终用 web search 补充指数和板块数据"""
        # 先从数据库取个股快照
        db_data = self._collect_market_from_db(session, period_start, period_end)

        # 始终执行 web search：数据库只有个股快照，没有大盘指数和板块排行
        logger.info("启动 Web 搜索获取大盘指数和板块数据...")
        try:
            from report_engine.market_overview import collect_market_overview
            web_data = collect_market_overview()
            db_data["web_search"] = web_data
            db_data["data_source"] = "db + web_search"
            logger.info(f"Web 搜索完成: {len(web_data.get('indices', []))} 个指数, "
                        f"{len(web_data.get('sectors', []))} 个板块, "
                        f"{len(web_data.get('concepts', []))} 个概念板块, "
                        f"{len(web_data.get('money_flow', []))} 个资金流向, "
                        f"{len(web_data.get('news', []))} 条新闻")
        except Exception as e:
            logger.warning(f"Web 搜索失败，仅使用数据库数据: {e}")
            db_data["data_source"] = "db_only (web_search_failed)"

        # 可选：真·联网搜索补充定性段外部观点（默认关）。追加进 web_search.news，
        # category='global' 自动并入 Ch3「全球财经」块渲染，下游零改。
        if enable_web_search:
            try:
                from acquisition.websearch import web_search as _ws
                ws = db_data.setdefault("web_search", {})
                ws.setdefault("news", [])
                hits = _ws("A股 市场 最新 解读 机构观点", max_results=8)
                ws["news"].extend([{
                    "title": h.get("title", ""),
                    "body": (h.get("snippet") or "")[:300],
                    "source": (h.get("source", "") or "").replace("ddg_", "") or "web",
                    "time": "",
                    "category": "global",
                    "lang": "zh",
                    "url": h.get("url", ""),
                } for h in hits])
                logger.info(f"定性段联网补充: {len(hits)} 条外部观点")
            except Exception as e:
                logger.warning(f"report 定性段联网补充失败，跳过: {e}")

        return db_data

    def _collect_market_from_db(self, session, period_start: date, period_end: date) -> Dict:
        """从数据库收集市场数据 — 仅取今天最新快照，提取丰富统计"""
        try:
            # snapshot_time 现已是 naive UTC，「今天」按 A 股市场日切成 UTC 半开区间
            today = market_today(A_SHARE)
            today_start, today_end = market_day_bounds(A_SHARE, today)

            # 取今天最新的快照时间
            latest_time = session.query(func.max(RealtimeSnapshot.snapshot_time)).filter(
                RealtimeSnapshot.snapshot_time >= today_start,
                RealtimeSnapshot.snapshot_time < today_end,
            ).scalar()

            snapshot_stats: Dict = {
                "latest_time": None, "total": 0,
                "up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0,
                "distribution": {},
                "total_amount": 0,
                "top_amount": [],
                "top_turnover": [],
                "limit_up_list": [],
                "limit_down_list": [],
            }

            if latest_time:
                snapshots = session.query(RealtimeSnapshot).filter(
                    RealtimeSnapshot.snapshot_time == latest_time
                ).all()

                snapshot_stats["latest_time"] = str(latest_time)
                snapshot_stats["total"] = len(snapshots)

                limit_up_stocks = []
                limit_down_stocks = []

                for s in snapshots:
                    pct = s.change_pct or 0
                    # 涨跌停按板块阈值判（主板10% / 创业板·科创板20%），真源 common.limit_rules；
                    # 此前全市场一刀切 9.9。刻意不传 name（不启用 ST 的 5% 阈值），
                    # 原因见 realtime.compute_statistics 的说明。
                    if is_limit_up(s.symbol, pct):
                        snapshot_stats["limit_up"] += 1
                        limit_up_stocks.append(s)
                    elif is_limit_down(s.symbol, pct):
                        snapshot_stats["limit_down"] += 1
                        limit_down_stocks.append(s)

                    # 涨跌家数**包含**涨跌停股（市场惯例，也与 compute_statistics
                    # 和下面的 DailyQuote 回退路径一致）。此前 elif 链让涨停股
                    # 既不进 up 也不进 down，上涨家数系统性偏少。
                    if pct > 0:
                        snapshot_stats["up"] += 1
                    elif pct < 0:
                        snapshot_stats["down"] += 1
                    else:
                        snapshot_stats["flat"] += 1

                    snapshot_stats["total_amount"] += (s.amount or 0)

                # 涨跌幅分布
                pcts = [s.change_pct for s in snapshots if s.change_pct is not None]
                snapshot_stats["distribution"] = {
                    "gt5": sum(1 for p in pcts if p > 5),
                    "3to5": sum(1 for p in pcts if 3 < p <= 5),
                    "1to3": sum(1 for p in pcts if 1 < p <= 3),
                    "0to1": sum(1 for p in pcts if 0 < p <= 1),
                    "flat": sum(1 for p in pcts if p == 0),
                    "neg1to0": sum(1 for p in pcts if -1 <= p < 0),
                    "neg3to1": sum(1 for p in pcts if -3 <= p < -1),
                    "neg5to3": sum(1 for p in pcts if -5 <= p < -3),
                    "lt_neg5": sum(1 for p in pcts if p < -5),
                }

                # 成交额 Top 10
                by_amount = sorted(snapshots, key=lambda s: s.amount or 0, reverse=True)
                snapshot_stats["top_amount"] = [
                    {"symbol": s.symbol, "name": s.name, "price": s.price,
                     "change_pct": s.change_pct, "amount": s.amount}
                    for s in by_amount[:10]
                ]

                # 换手率 Top 10
                by_turnover = sorted(snapshots, key=lambda s: s.turnover or 0, reverse=True)
                snapshot_stats["top_turnover"] = [
                    {"symbol": s.symbol, "name": s.name, "price": s.price,
                     "change_pct": s.change_pct, "turnover": s.turnover}
                    for s in by_turnover[:10]
                ]

                # 涨停明细（按成交额排序）
                limit_up_stocks.sort(key=lambda s: s.amount or 0, reverse=True)
                snapshot_stats["limit_up_list"] = [
                    {"symbol": s.symbol, "name": s.name, "price": s.price,
                     "change_pct": s.change_pct, "amount": s.amount}
                    for s in limit_up_stocks[:15]
                ]

                # 跌停明细
                limit_down_stocks.sort(key=lambda s: s.amount or 0, reverse=True)
                snapshot_stats["limit_down_list"] = [
                    {"symbol": s.symbol, "name": s.name, "price": s.price,
                     "change_pct": s.change_pct, "amount": s.amount}
                    for s in limit_down_stocks[:10]
                ]

                logger.info(f"今日实时快照: {len(snapshots)} 条 (时间: {latest_time}), "
                            f"涨{snapshot_stats['up']}/跌{snapshot_stats['down']}, "
                            f"总成交额{snapshot_stats['total_amount']/1e8:.0f}亿")
            else:
                logger.info("今日无实时快照数据，尝试从 DailyQuote 回退计算涨跌广度")
                try:
                    # 查最新两个交易日
                    latest_dates = (
                        session.query(DailyQuote.date)
                        .distinct()
                        .order_by(DailyQuote.date.desc())
                        .limit(2)
                        .all()
                    )
                    if len(latest_dates) >= 2:
                        date_today = latest_dates[0][0]
                        date_prev = latest_dates[1][0]

                        # 取最新日收盘价
                        today_quotes = {
                            q.symbol: q.close
                            for q in session.query(DailyQuote.symbol, DailyQuote.close)
                            .filter(DailyQuote.date == date_today).all()
                        }
                        prev_quotes = {
                            q.symbol: q.close
                            for q in session.query(DailyQuote.symbol, DailyQuote.close)
                            .filter(DailyQuote.date == date_prev).all()
                        }

                        # 计算涨跌幅（留住 symbol：涨跌停要按板块阈值判）
                        changes: List[Tuple[str, float]] = []
                        for symbol, close_today in today_quotes.items():
                            close_prev = prev_quotes.get(symbol)
                            if close_prev and close_prev > 0 and close_today:
                                changes.append((symbol, (close_today - close_prev) / close_prev * 100))
                        pcts = [p for _, p in changes]

                        if pcts:
                            snapshot_stats["latest_time"] = f"{date_today} (DailyQuote回退)"
                            snapshot_stats["total"] = len(pcts)
                            snapshot_stats["up"] = sum(1 for p in pcts if p > 0)
                            snapshot_stats["down"] = sum(1 for p in pcts if p < 0)
                            snapshot_stats["flat"] = sum(1 for p in pcts if p == 0)
                            snapshot_stats["limit_up"] = sum(1 for sym, p in changes if is_limit_up(sym, p))
                            snapshot_stats["limit_down"] = sum(1 for sym, p in changes if is_limit_down(sym, p))
                            snapshot_stats["distribution"] = {
                                "gt5": sum(1 for p in pcts if p > 5),
                                "3to5": sum(1 for p in pcts if 3 < p <= 5),
                                "1to3": sum(1 for p in pcts if 1 < p <= 3),
                                "0to1": sum(1 for p in pcts if 0 < p <= 1),
                                "flat": sum(1 for p in pcts if p == 0),
                                "neg1to0": sum(1 for p in pcts if -1 <= p < 0),
                                "neg3to1": sum(1 for p in pcts if -3 <= p < -1),
                                "neg5to3": sum(1 for p in pcts if -5 <= p < -3),
                                "lt_neg5": sum(1 for p in pcts if p < -5),
                            }
                            logger.info(f"DailyQuote 回退涨跌广度: {len(pcts)} 只 ({date_today} vs {date_prev}), "
                                        f"涨{snapshot_stats['up']}/跌{snapshot_stats['down']}")
                        else:
                            logger.info("DailyQuote 无法计算涨跌广度（无重叠标的）")
                    else:
                        logger.info("DailyQuote 不足两个交易日，无法回退")
                except Exception as e:
                    logger.warning(f"DailyQuote 回退失败: {e}")

            # 区间涨跌统计
            period_stats: Dict = {"avg_change_pct": None, "stocks_with_data": 0}
            quotes = session.query(
                DailyQuote.symbol,
                func.min(DailyQuote.close).label("first_close"),
                func.max(DailyQuote.date).label("last_date"),
            ).filter(
                DailyQuote.date >= period_start,
                DailyQuote.date <= period_end,
            ).group_by(DailyQuote.symbol).all()
            period_stats["stocks_with_data"] = len(quotes)

            return {"snapshot": snapshot_stats, "period": period_stats}
        except Exception as e:
            logger.warning(f"收集市场总览数据失败: {e}")
            return {"snapshot": {}, "period": {}, "error": str(e)}

    # ------------------------------------------------------------------
    def _collect_signals(self, session, period_start: date, period_end: date,
                         held_symbols: Optional[set] = None) -> Dict:
        """收集信号数据 — 买卖分离，同时为持仓个股收集全部信号（不受 Top N 限制）"""
        try:
            signals = session.query(Signal).filter(
                Signal.date >= period_start,
                Signal.date <= period_end,
            ).order_by(Signal.strength.desc()).all()

            total = len(signals)
            buy = len([s for s in signals if s.signal_type == "BUY"])
            sell = len([s for s in signals if s.signal_type == "SELL"])
            avg_strength = sum(s.strength for s in signals) / total if total else 0

            # 按策略分组
            by_strategy: Dict = {}
            for s in signals:
                key = s.strategy or "unknown"
                if key not in by_strategy:
                    by_strategy[key] = {"buy": 0, "sell": 0, "total": 0, "avg_strength": 0, "strengths": []}
                by_strategy[key]["total"] += 1
                by_strategy[key]["buy" if s.signal_type == "BUY" else "sell"] += 1
                by_strategy[key]["strengths"].append(s.strength)

            for v in by_strategy.values():
                v["avg_strength"] = round(sum(v["strengths"]) / len(v["strengths"]), 4) if v["strengths"] else 0
                del v["strengths"]

            # 买入信号 Top 15
            buy_signals = [s for s in signals if s.signal_type == "BUY"][:15]
            top_buy_signals = self._format_signal_list(session, buy_signals)

            # 卖出信号 Top 10
            sell_signals = [s for s in signals if s.signal_type == "SELL"][:10]
            top_sell_signals = self._format_signal_list(session, sell_signals)

            # 持仓个股全部信号（不受 Top N 限制，供 Ch5 持仓诊断使用）
            held_stock_signals = []
            if held_symbols:
                held_sigs = [s for s in signals if s.symbol in held_symbols]
                held_stock_signals = self._format_signal_list(session, held_sigs)
                logger.info(f"持仓个股信号: {len(held_stock_signals)} 条 (覆盖 "
                            f"{len({s.symbol for s in held_sigs})}/{len(held_symbols)} 只持仓)")

            return {
                "total": total, "buy": buy, "sell": sell,
                "avg_strength": round(avg_strength, 4),
                "by_strategy": by_strategy,
                "top_buy_signals": top_buy_signals,
                "top_sell_signals": top_sell_signals,
                "held_stock_signals": held_stock_signals,
            }
        except Exception as e:
            logger.warning(f"收集信号数据失败: {e}")
            return {"total": 0, "error": str(e)}

    def _format_signal_list(self, session, signals: list) -> List[Dict]:
        """格式化信号列表，附带 StockInfo"""
        result = []
        for s in signals:
            reasons = []
            if s.reasons:
                try:
                    reasons = json.loads(s.reasons)
                except Exception:
                    reasons = [s.reasons]

            info = session.query(StockInfo).filter(StockInfo.symbol == s.symbol).first()

            item = {
                "symbol": s.symbol,
                "name": info.name if info else s.symbol,
                "date": str(s.date),
                "type": s.signal_type,
                "strength": s.strength,
                "strategy": s.strategy,
                "price": s.price,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "reasons": reasons,
            }
            if info:
                item["industry"] = getattr(info, "industry", None)
                item["sector"] = getattr(info, "sector", None)
            result.append(item)
        return result

    # ------------------------------------------------------------------
    def _collect_backtest(self, session) -> Dict:
        """收集回测对比数据（最近的各策略结果）"""
        try:
            results = session.query(BacktestResult).join(BacktestTask).order_by(
                BacktestResult.created_at.desc()
            ).limit(20).all()

            comparisons = []
            seen_strategies = set()
            for r in results:
                task = session.query(BacktestTask).filter(BacktestTask.task_id == r.task_id).first()
                if not task:
                    continue
                strategy = task.strategy_type
                if strategy in seen_strategies:
                    continue
                seen_strategies.add(strategy)
                comparisons.append({
                    "strategy": strategy,
                    "total_return_pct": r.total_return_pct,
                    "annual_return": r.annual_return,
                    "sharpe_ratio": r.sharpe_ratio,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "win_rate": r.win_rate,
                    "total_trades": r.total_trades,
                    "profit_factor": r.profit_factor,
                })

            return {"strategies": comparisons}
        except Exception as e:
            logger.warning(f"收集回测数据失败: {e}")
            return {"strategies": [], "error": str(e)}

    # ------------------------------------------------------------------
    def _build_backtest_stats_map(self, session) -> Dict:
        """从 BacktestResult 构建策略回测统计映射

        Returns:
            {"MACD": {"sharpe_ratio": 1.2, "win_rate": 55, "total_trades": 120}, ...}
        """
        stats_map: Dict = {}
        try:
            results = session.query(BacktestResult).join(BacktestTask).order_by(
                BacktestResult.created_at.desc()
            ).limit(20).all()

            for r in results:
                task = session.query(BacktestTask).filter(BacktestTask.task_id == r.task_id).first()
                if not task or not task.strategy_type:
                    continue
                strategy = task.strategy_type
                if strategy in stats_map:
                    continue  # 每个策略只取最新一条
                stats_map[strategy] = {
                    "sharpe_ratio": r.sharpe_ratio,
                    "win_rate": r.win_rate,
                    "total_trades": r.total_trades,
                }
            logger.info(f"回测统计映射: {list(stats_map.keys())}")
        except Exception as e:
            logger.warning(f"构建回测统计映射失败: {e}")
        return stats_map

    def _collect_top_stocks(self, session, period_start: date, period_end: date,
                            held_symbols: Optional[set] = None,
                            want: tuple = ("buy", "sell")) -> Dict:
        """收集重点个股数据 — 综合评分排序，买入推荐 10 只 + 卖出预警 3 只

        Args:
            held_symbols: 用户当前持仓股代码集合，买入推荐会排除这些标的（Ch5 已分析）
            want: 只算需要的那半边。买入推荐这一半最贵（打分 + 逐股庄股风险 + 逐股新闻），
                持仓诊断只用得上 sell_warnings，别让它白跑一遍买入侧。
        """
        try:
            scorer = SignalScorer()
            held_symbols = held_symbols or set()
            want_buy = "buy" in want
            want_sell = "sell" in want

            # ---- Step 4: 构建策略回测统计 ----
            backtest_stats = self._build_backtest_stats_map(session) if want_buy else {}

            # ---- 买入推荐：综合评分排序（排除持仓股）----
            all_buy_signals = session.query(Signal).filter(
                Signal.date >= period_start,
                Signal.date <= period_end,
                Signal.signal_type == "BUY",
            ).all() if want_buy else []

            # 过滤掉持仓股，Ch5 已做持仓诊断，Ch8 只推荐新机会
            if held_symbols:
                before_count = len(all_buy_signals)
                all_buy_signals = [s for s in all_buy_signals if s.symbol not in held_symbols]
                filtered = before_count - len(all_buy_signals)
                if filtered > 0:
                    logger.info(f"买入推荐已排除 {filtered} 条持仓股信号（持仓: {', '.join(sorted(held_symbols))}）")

            # ---- 预计算候选股庄股风险，传入 scorer ----
            stock_analysis: Dict = {}
            candidate_symbols = set(s.symbol for s in all_buy_signals)
            for sym in candidate_symbols:
                try:
                    sym_quotes = session.query(DailyQuote).filter(
                        DailyQuote.symbol == sym,
                        DailyQuote.date <= period_end,
                    ).order_by(DailyQuote.date.desc()).limit(20).all()
                    if len(sym_quotes) >= 5:
                        aq = [{"date": str(q.date), "open": q.open, "high": q.high,
                               "low": q.low, "close": q.close, "volume": q.volume,
                               "turnover": getattr(q, "turnover", None)}
                              for q in reversed(sym_quotes)]
                        risk = StockAnalyzer.analyze_manipulation_risk(aq)
                        if risk:
                            stock_analysis[sym] = risk
                except Exception:
                    pass

            ranked_buys = scorer.rank_buy_signals(
                all_buy_signals, period_end, top_n=10, min_score=65.0,
                backtest_stats=backtest_stats,
                stock_analysis=stock_analysis,
            )

            buy_recommendations = []
            for ranked in ranked_buys:
                best_sig = ranked["best_signal"]
                stock = self._enrich_stock_data(session, best_sig, period_end, quote_days=10)
                # 附加综合评分信息
                stock["composite_score"] = ranked["composite_score"]
                stock["score_breakdown"] = {
                    k: v for k, v in ranked["score_breakdown"].items()
                }
                stock["resonance_strategies"] = list(set(
                    s.strategy for s in ranked["signals"]
                ))
                stock["resonance_count"] = len(stock["resonance_strategies"])
                buy_recommendations.append(stock)

            # ---- 卖出预警：共振+强度排序 ----
            all_sell_signals = session.query(Signal).filter(
                Signal.date >= period_start,
                Signal.date <= period_end,
                Signal.signal_type == "SELL",
            ).all() if want_sell else []

            ranked_sells = scorer.rank_sell_signals(all_sell_signals, period_end, top_n=3)

            sell_warnings = []
            for ranked in ranked_sells:
                best_sig = ranked["best_signal"]
                stock = self._enrich_stock_data(session, best_sig, period_end, quote_days=5)
                stock["composite_score"] = ranked["composite_score"]
                stock["resonance_strategies"] = list(set(
                    s.strategy for s in ranked["signals"]
                ))
                stock["resonance_count"] = len(stock["resonance_strategies"])
                sell_warnings.append(stock)

            # ---- 批量获取基本面数据（PE/PB/市值/ROE） ----
            all_symbols = [s["symbol"] for s in buy_recommendations + sell_warnings]
            fundamentals: Dict = {}
            if all_symbols:
                try:
                    from acquisition.markets.financial import fetch_fundamentals
                    fundamentals = fetch_fundamentals(all_symbols)
                    logger.info(f"基本面数据: 获取 {len(fundamentals)}/{len(all_symbols)} 只")
                except Exception as e:
                    logger.warning(f"获取基本面数据失败: {e}")

            # 附加基本面到每只标的
            for stock in buy_recommendations + sell_warnings:
                fund = fundamentals.get(stock["symbol"], {})
                if fund:
                    stock["fundamentals"] = fund

            # ── Step 2: 为买入推荐标的进行风险分层 ──
            for stock in buy_recommendations:
                stock["risk_tier"] = self._classify_risk_tier(stock)
                logger.debug(f"风险分级 {stock['symbol']}: {stock['risk_tier']['label']} "
                             f"(得分{stock['risk_tier']['score']})")

            return {
                "buy_recommendations": buy_recommendations,
                "sell_warnings": sell_warnings,
            }
        except Exception as e:
            logger.warning(f"收集重点个股数据失败: {e}")
            return {"buy_recommendations": [], "sell_warnings": []}

    def _classify_risk_tier(self, stock: Dict) -> Dict:
        """对推荐标的进行风险分层（打分制）

        Returns:
            {"tier": "core"|"satellite"|"speculative",
             "label": "核心仓"|"卫星仓"|"投机仓",
             "max_position_pct": 0.15|0.08|0.03,
             "score": int, "reasons": [str]}
        """
        score = 0
        reasons = []

        # ── 基本面 ──
        fund = stock.get("fundamentals") or {}
        pe = fund.get("pe_ttm")
        if pe is not None:
            if 0 < pe < 80:
                score += 2
                reasons.append(f"PE(TTM)={pe:.1f} 合理")
            elif pe < 0 or pe > 200:
                score -= 2
                reasons.append(f"PE(TTM)={pe:.1f} 异常")
        name = stock.get("name", "")
        if "ST" in name or "*ST" in name:
            score -= 3
            reasons.append("ST股票，高风险")

        # ── 共振度 ──
        composite = stock.get("composite_score", 0)
        if composite >= 80:
            score += 2
            reasons.append(f"综合评分{composite}分，优秀")
        resonance_count = stock.get("resonance_count", 0)
        if resonance_count >= 3:
            score += 2
            reasons.append(f"{resonance_count}策略共振")
        elif resonance_count <= 1:
            score -= 1
            reasons.append("仅单策略信号")

        # ── 量能 ──
        breakdown = stock.get("score_breakdown", {})
        vol_score = breakdown.get("volume", {}).get("score", 0)
        if vol_score >= 75:
            score += 1
            reasons.append("量能充足")

        # ── 分级 ──
        if score >= 4:
            tier = "core"
            label = "核心仓"
            max_pct = 0.15
        elif score >= 1:
            tier = "satellite"
            label = "卫星仓"
            max_pct = 0.08
        else:
            tier = "speculative"
            label = "投机仓"
            max_pct = 0.03

        return {
            "tier": tier,
            "label": label,
            "max_position_pct": max_pct,
            "score": score,
            "reasons": reasons,
        }

    def _enrich_stock_data(self, session, sig: Signal, period_end: date, quote_days: int = 10) -> Dict:
        """丰富单只股票数据：StockInfo + 近 N 日行情 + 20日价格统计"""
        info = session.query(StockInfo).filter(StockInfo.symbol == sig.symbol).first()

        reasons = []
        if sig.reasons:
            try:
                reasons = json.loads(sig.reasons)
            except Exception:
                reasons = [sig.reasons]

        # 近 N 日行情
        quotes = session.query(DailyQuote).filter(
            DailyQuote.symbol == sig.symbol,
            DailyQuote.date <= period_end,
        ).order_by(DailyQuote.date.desc()).limit(20).all()

        quote_summary = []
        for q in quotes[:quote_days]:
            quote_summary.append({
                "date": str(q.date), "open": q.open, "high": q.high,
                "low": q.low, "close": q.close, "volume": q.volume,
                "turnover": getattr(q, "turnover", None),
            })

        # 20日价格统计
        price_stats: Dict = {}
        if quotes:
            closes = [q.close for q in quotes if q.close]
            highs = [q.high for q in quotes if q.high]
            lows = [q.low for q in quotes if q.low]
            if closes:
                price_stats = {
                    "high_20d": max(highs) if highs else None,
                    "low_20d": min(lows) if lows else None,
                    "avg_20d": round(sum(closes) / len(closes), 2),
                    "latest_close": closes[0] if closes else None,
                }

        stock = {
            "symbol": sig.symbol,
            "name": info.name if info else sig.symbol,
            "signal_type": sig.signal_type,
            "strength": sig.strength,
            "price": sig.price,
            "stop_loss": sig.stop_loss,
            "take_profit": sig.take_profit,
            "strategy": sig.strategy,
            "reasons": reasons,
            "recent_quotes": quote_summary,
            "price_stats": price_stats,
        }

        if info:
            stock["industry"] = getattr(info, "industry", None)
            stock["sector"] = getattr(info, "sector", None)

        # 技术指标快照：用已查询的 20 日行情计算指标
        if len(quotes) >= 5:
            try:
                indicator_snapshot = self._build_indicator_snapshot(quotes)
                if indicator_snapshot:
                    stock["indicators"] = indicator_snapshot
            except Exception as e:
                logger.debug(f"计算 {sig.symbol} 技术指标快照失败: {e}")

        # 抓取个股新闻（每只最多2条最重要的，内容300字）
        try:
            from news_engine.fetcher import NewsFetcher
            fetcher = NewsFetcher()
            news_list = fetcher.fetch_a_share_news(sig.symbol)
            stock["news"] = [
                {
                    "title": n["title"],
                    "content": (n.get("content") or "")[:300],
                    "source": n.get("source", ""),
                    "published_at": str(n.get("published_at", "")),
                }
                for n in news_list[:2]
            ]
        except Exception as e:
            logger.debug(f"抓取 {sig.symbol} 新闻失败: {e}")
            stock["news"] = []

        # ── StockAnalyzer 深度分析（4 维度，独立容错） ──
        if len(quotes) >= 5:
            # 构建升序 analysis_quotes（quotes 是 desc 的，需反转）
            analysis_quotes = []
            for q in reversed(quotes):
                analysis_quotes.append({
                    "date": str(q.date), "open": q.open, "high": q.high,
                    "low": q.low, "close": q.close, "volume": q.volume,
                    "turnover": getattr(q, "turnover", None),
                })

            fund = stock.get("fundamentals")
            atr_val = stock.get("indicators", {}).get("atr") if stock.get("indicators") else None
            entry_price = sig.price

            try:
                stock["manipulation_risk"] = StockAnalyzer.analyze_manipulation_risk(analysis_quotes, fund)
            except Exception as e:
                logger.debug(f"{sig.symbol} 庄股分析失败: {e}")

            try:
                stock["small_cap_profile"] = StockAnalyzer.analyze_small_cap_profile(analysis_quotes, fund)
            except Exception as e:
                logger.debug(f"{sig.symbol} 小盘画像失败: {e}")

            try:
                stock["dynamic_levels"] = StockAnalyzer.calculate_dynamic_levels(
                    entry_price, analysis_quotes, atr_val, sig.signal_type
                )
            except Exception as e:
                logger.debug(f"{sig.symbol} 动态止盈止损失败: {e}")

            try:
                stock["market_behavior"] = StockAnalyzer.analyze_market_behavior(analysis_quotes)
            except Exception as e:
                logger.debug(f"{sig.symbol} 机构行为分析失败: {e}")

        return stock

    def _build_indicator_snapshot(self, quotes: list) -> Optional[Dict]:
        """从 DailyQuote 列表构建技术指标快照"""
        # quotes 是按 date desc 排列的，需要反转为 asc
        rows = []
        for q in reversed(quotes):
            rows.append({
                "date": str(q.date),
                "open": q.open,
                "high": q.high,
                "low": q.low,
                "close": q.close,
                "volume": q.volume or 0,
            })

        if len(rows) < 5:
            return None

        df = pd.DataFrame(rows)
        df = AnalysisEngine().add_indicators(df)
        latest = df.iloc[-1]

        def _safe(val):
            if val is None or pd.isna(val):
                return None
            return round(float(val), 2)

        # 均线排列状态
        ma5 = _safe(latest.get('ma5'))
        ma10 = _safe(latest.get('ma10'))
        ma20 = _safe(latest.get('ma20'))

        ma_status = "数据不足"
        if ma5 is not None and ma10 is not None and ma20 is not None:
            if ma5 > ma10 > ma20:
                ma_status = "多头排列"
            elif ma5 < ma10 < ma20:
                ma_status = "空头排列"
            else:
                ma_status = "交叉排列"

        # MACD 零轴位置
        dif = _safe(latest.get('macd_dif'))
        dea = _safe(latest.get('macd_dea'))
        macd_bar = _safe(latest.get('macd'))
        macd_position = "数据不足"
        if dif is not None:
            if dif > 0 and dea is not None and dea > 0:
                macd_position = "零轴上方"
            elif dif < 0 and dea is not None and dea < 0:
                macd_position = "零轴下方"
            else:
                macd_position = "零轴附近"

        # RSI 区域
        rsi = _safe(latest.get('rsi'))
        rsi_zone = "数据不足"
        if rsi is not None:
            if rsi > 70:
                rsi_zone = "超买区"
            elif rsi < 30:
                rsi_zone = "超卖区"
            elif rsi > 55:
                rsi_zone = "偏强区"
            elif rsi < 45:
                rsi_zone = "偏弱区"
            else:
                rsi_zone = "中性区"

        # 布林带位置
        boll_upper = _safe(latest.get('boll_upper'))
        boll_middle = _safe(latest.get('boll_mid'))
        boll_lower = _safe(latest.get('boll_lower'))
        close = _safe(latest.get('close'))
        boll_position = "数据不足"
        if close is not None and boll_upper is not None and boll_lower is not None:
            if close >= boll_upper:
                boll_position = "上轨附近"
            elif close <= boll_lower:
                boll_position = "下轨附近"
            elif boll_middle is not None and close > boll_middle:
                boll_position = "中轨上方"
            else:
                boll_position = "中轨下方"

        # 量比描述
        vr = _safe(latest.get('volume_ratio'))
        vr_desc = "数据不足"
        if vr is not None:
            if vr >= 2.0:
                vr_desc = "显著放量"
            elif vr >= 1.5:
                vr_desc = "温和放量"
            elif vr >= 0.8:
                vr_desc = "平量"
            else:
                vr_desc = "缩量"

        snapshot = {
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma_status": ma_status,
            "macd_dif": dif,
            "macd_dea": dea,
            "macd_bar": macd_bar,
            "macd_position": macd_position,
            "rsi": rsi,
            "rsi_zone": rsi_zone,
            "kdj_k": _safe(latest.get('kdj_k')),
            "kdj_d": _safe(latest.get('kdj_d')),
            "kdj_j": _safe(latest.get('kdj_j')),
            "boll_upper": boll_upper,
            "boll_middle": boll_middle,
            "boll_lower": boll_lower,
            "boll_position": boll_position,
            "volume_ratio": vr,
            "volume_ratio_desc": vr_desc,
            "atr": _safe(latest.get('atr')),
        }

        return snapshot

    # ------------------------------------------------------------------
    def _collect_portfolio(self, session) -> Dict:
        """收集用户当前真实持仓数据（来自手动交易记录）"""
        try:
            calculator = PortfolioCalculator()
            positions = calculator.get_current_positions()
            stats = calculator.get_performance_stats()

            total_market_value = sum(p.get("market_value", 0) or 0 for p in positions)
            total_cost = sum(p.get("total_cost", 0) or 0 for p in positions)
            total_unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in positions)

            # 从 UserSettings 读取总资金
            settings_session = get_session()
            try:
                row = settings_session.query(UserSettings).filter(UserSettings.key == "total_capital").first()
                total_capital = float(row.value) if row else 200000.0
            except Exception:
                total_capital = 200000.0
            finally:
                settings_session.close()

            # 仓位占比 = 当前市值 / 总资金
            total_invested = stats.get("total_invested", 0)
            position_ratio = round(total_market_value / total_capital * 100, 1) if total_capital > 0 else 0

            # 批量获取持仓股基本面（PE/PB/市值/ROE）
            port_symbols = [p["symbol"] for p in positions if p.get("symbol")]
            if port_symbols:
                try:
                    from acquisition.markets.financial import fetch_fundamentals
                    fundamentals = fetch_fundamentals(port_symbols)
                    for p in positions:
                        fund = fundamentals.get(p.get("symbol", ""), {})
                        if fund:
                            p["fundamentals"] = fund
                    logger.info(f"持仓基本面: 获取 {len(fundamentals)}/{len(port_symbols)} 只")
                except Exception as e:
                    logger.warning(f"获取持仓基本面失败: {e}")

            # ── 为每只持仓添加 StockAnalyzer 深度分析 ──
            for p in positions:
                sym = p.get("symbol")
                if not sym:
                    continue
                try:
                    p_quotes = session.query(DailyQuote).filter(
                        DailyQuote.symbol == sym,
                    ).order_by(DailyQuote.date.desc()).limit(20).all()
                    if len(p_quotes) >= 5:
                        aq = [{"date": str(q.date), "open": q.open, "high": q.high,
                               "low": q.low, "close": q.close, "volume": q.volume,
                               "turnover": getattr(q, "turnover", None)}
                              for q in reversed(p_quotes)]
                        fund = p.get("fundamentals")
                        avg_cost = p.get("avg_cost")
                        atr_val = None

                        try:
                            p["manipulation_risk"] = StockAnalyzer.analyze_manipulation_risk(aq, fund)
                        except Exception:
                            pass
                        try:
                            p["small_cap_profile"] = StockAnalyzer.analyze_small_cap_profile(aq, fund)
                        except Exception:
                            pass
                        try:
                            p["dynamic_levels"] = StockAnalyzer.calculate_dynamic_levels(
                                avg_cost, aq, atr_val, "BUY"
                            )
                        except Exception:
                            pass
                        try:
                            p["market_behavior"] = StockAnalyzer.analyze_market_behavior(aq)
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"持仓 {sym} 深度分析失败: {e}")

            logger.info(f"持仓采集: {len(positions)} 只，总市值 {total_market_value:.0f}，"
                        f"总资金 {total_capital:.0f}，仓位 {position_ratio}%")

            # ── 仓位预算计算（Step 1: 解决仓位管理自相矛盾问题） ──
            # ⚠️ 走 adapter 不直读 RISK_CONFIG：单股上限的真值在 UserSettings 里
            # （Jason 实际设的是 0.5，而 RISK_CONFIG 的默认是 0.2）——直读会让周报
            # 印出一个跟引擎实际用的不一样的「单股上限 20%」。总仓位上限则是硬底线，
            # 由 `get_max_total_position_pct()` 独家提供（S0 §1.3）。
            from trading_engine.risk.adapter import (
                get_max_position_pct,
                get_max_total_position_pct,
            )
            max_total_pct = get_max_total_position_pct()
            max_single_pct = get_max_position_pct()
            max_total_amount = total_capital * max_total_pct
            remaining_budget = max(0, max_total_amount - total_market_value)
            remaining_budget_pct = round(remaining_budget / total_capital * 100, 1) if total_capital > 0 else 0
            max_single_amount = total_capital * max_single_pct
            can_buy_new = remaining_budget > 0
            max_new_positions = int(remaining_budget / max_single_amount) if max_single_amount > 0 and can_buy_new else 0

            budget = {
                "total_capital": total_capital,
                "current_position_pct": position_ratio,
                "max_total_pct": round(max_total_pct * 100, 0),
                "remaining_budget": round(remaining_budget, 2),
                "remaining_budget_pct": remaining_budget_pct,
                "max_single_pct": round(max_single_pct * 100, 0),
                "max_single_amount": round(max_single_amount, 2),
                "can_buy_new": can_buy_new,
                "max_new_positions": max_new_positions,
            }

            logger.info(f"仓位预算: 剩余可投 {remaining_budget:.0f}元 ({remaining_budget_pct}%), "
                        f"{'可新建仓' if can_buy_new else '仓位已满'}, 最多新增 {max_new_positions} 只")

            # ── Step 3: 组合风险分析 ──
            risk_analysis = {}
            if positions:
                try:
                    from portfolio.risk_analyzer import PortfolioRiskAnalyzer
                    analyzer = PortfolioRiskAnalyzer()
                    risk_analysis = analyzer.analyze(positions, lookback_days=60)
                except Exception as e:
                    logger.warning(f"组合风险分析失败: {e}")
                    risk_analysis = {"error": str(e), "risk_level": "N/A"}

            return {
                "positions": positions,
                "stats": {
                    "total_positions": len(positions),
                    "total_market_value": round(total_market_value, 2),
                    "total_cost": round(total_cost, 2),
                    "total_unrealized_pnl": round(total_unrealized, 2),
                    "total_unrealized_pnl_pct": round(total_unrealized / total_cost * 100, 2) if total_cost > 0 else 0,
                    "total_realized_pnl": stats.get("realized_pnl", 0),
                    "total_invested": total_invested,
                    "total_capital": total_capital,
                    "position_ratio": position_ratio,
                },
                "budget": budget,
                "risk_analysis": risk_analysis,
            }
        except Exception as e:
            logger.warning(f"收集持仓数据失败: {e}")
            return {"positions": [], "stats": {}, "error": str(e)}

    # ------------------------------------------------------------------
    def _collect_previous_recommendations(self, session, current_period_end: date, report_type: str = "weekly") -> Dict:
        """收集上一批 report_picks 推荐标的及其后续表现，形成推荐回顾闭环。

        数据源是 `DecisionLog`（见 report_engine/picks_log.py 的说明）。`report_type`
        保留在签名里只为兼容旧 façade 的调用，不再参与筛选——「上期」现在指
        「上一批推荐」，不是「上一份同周期报告」。
        """
        try:
            buy_recs, report_date = picks_log.fetch_last_picks(session, current_period_end)

            if not buy_recs:
                return {"has_previous": False}

            recommendations = []
            winning = 0
            losing = 0
            no_data = 0
            total_return = 0.0

            for rec in buy_recs:
                symbol = rec.get("symbol", "")
                rec_price = rec.get("price")
                stop_loss = rec.get("stop_loss")
                take_profit = rec.get("take_profit")

                if not symbol:
                    continue

                # Bug#3 — price 缺失时仍纳入汇总，标记为数据缺失
                if not rec_price:
                    logger.warning(f"上期推荐 {symbol} 缺少推荐价格，标记为数据缺失")
                    recommendations.append({
                        "symbol": symbol,
                        "name": rec.get("name", symbol),
                        "recommended_price": None,
                        "current_price": None,
                        "change_pct": 0,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "hit_stop_loss": False,
                        "hit_take_profit": False,
                        "period_high": None,
                        "period_low": None,
                        "status": "数据缺失",
                        "strategy": rec.get("strategy", ""),
                        "composite_score": rec.get("composite_score", ""),
                    })
                    no_data += 1
                    continue

                # 查询推荐日之后到现在的行情
                quotes = (
                    session.query(DailyQuote)
                    .filter(
                        DailyQuote.symbol == symbol,
                        DailyQuote.date > report_date,
                        DailyQuote.date <= current_period_end,
                    )
                    .order_by(DailyQuote.date.asc())
                    .all()
                )

                # Bug#2 — 无行情数据时标记为"无数据"，不纳入胜率计算
                if not quotes:
                    logger.warning(f"上期推荐 {symbol} 在 {report_date}~{current_period_end} 无行情数据")
                    recommendations.append({
                        "symbol": symbol,
                        "name": rec.get("name", symbol),
                        "recommended_price": rec_price,
                        "current_price": None,
                        "change_pct": 0,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "hit_stop_loss": False,
                        "hit_take_profit": False,
                        "period_high": None,
                        "period_low": None,
                        "status": "暂无行情数据",
                        "strategy": rec.get("strategy", ""),
                        "composite_score": rec.get("composite_score", ""),
                    })
                    no_data += 1
                    continue

                current_price = quotes[-1].close
                period_high = max(q.high for q in quotes)
                period_low = min(q.low for q in quotes)

                # 按日线顺序判断止盈/止损哪个先触发
                hit_stop_loss = False
                hit_take_profit = False
                first_trigger = None  # "tp" or "sl"

                for q in quotes:  # quotes 已按 date asc 排序
                    if not first_trigger and stop_loss and q.low <= stop_loss:
                        hit_stop_loss = True
                        first_trigger = "sl"
                        break  # 止损先触发，后续不可能再止盈
                    if not first_trigger and take_profit and q.high >= take_profit:
                        hit_take_profit = True
                        first_trigger = "tp"
                        break  # 止盈先触发，后续不可能再止损

                # 按先触发的价格计算收益（反映纪律执行效果）
                if first_trigger == "tp":
                    effective_price = take_profit
                elif first_trigger == "sl":
                    effective_price = stop_loss
                else:
                    effective_price = current_price

                change_pct = round((effective_price - rec_price) / rec_price * 100, 2)
                total_return += change_pct

                if change_pct > 0:
                    winning += 1
                elif change_pct < 0:
                    losing += 1

                # 状态判断
                if hit_take_profit:
                    status = "已触止盈"
                elif hit_stop_loss:
                    status = "已触止损"
                elif change_pct > 0:
                    status = "盈利中"
                elif change_pct < 0:
                    status = "亏损中"
                else:
                    status = "持平"

                recommendations.append({
                    "symbol": symbol,
                    "name": rec.get("name", symbol),
                    "recommended_price": rec_price,
                    "current_price": current_price,
                    "effective_price": effective_price,
                    "change_pct": change_pct,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "hit_stop_loss": hit_stop_loss,
                    "hit_take_profit": hit_take_profit,
                    "period_high": period_high,
                    "period_low": period_low,
                    "status": status,
                    "strategy": rec.get("strategy", ""),
                    "composite_score": rec.get("composite_score", ""),
                })

            total_recs = len(recommendations)
            valid_recs = total_recs - no_data  # 有有效行情的标的数
            avg_return = round(total_return / valid_recs, 2) if valid_recs > 0 else 0
            win_rate = round(winning / valid_recs * 100, 1) if valid_recs > 0 else 0

            logger.info(f"上期推荐回顾: {total_recs}只(有效{valid_recs}只), "
                        f"{winning}涨/{losing}跌/{no_data}无数据, "
                        f"胜率{win_rate}%, 平均涨幅{avg_return}%")

            return {
                "has_previous": True,
                "report_id": f"picks_{report_date.isoformat()}",
                "report_date": str(report_date),
                "report_title": f"上期买入推荐（{total_recs} 只）",
                "recommendations": recommendations,
                "summary": {
                    "total": total_recs,
                    "valid": valid_recs,
                    "winning": winning,
                    "losing": losing,
                    "no_data": no_data,
                    "flat": valid_recs - winning - losing,
                    "win_rate": win_rate,
                    "avg_return_pct": avg_return,
                },
            }
        except Exception as e:
            logger.warning(f"收集上期推荐数据失败: {e}")
            return {"has_previous": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 重大新闻关键词
    # ------------------------------------------------------------------
    _CRITICAL_KEYWORDS = [
        "央行", "降准", "降息", "加息", "LPR", "MLF", "逆回购",
        "国务院", "证监会", "银保监", "财政部",
        "暴雷", "退市", "ST", "立案", "违规", "处罚",
        "美联储", "Fed", "关税", "制裁", "战争", "冲突",
        "IPO", "注册制", "印花税", "熔断",
    ]

    def _analyze_news_sentiment(self, data: Dict) -> Dict:
        """对市场新闻和个股新闻进行 BERT 情感分析 + 重大新闻检测

        Returns:
            {
                "market_sentiment": {"positive": N, "negative": N, "neutral": N, "total": N, "tendency": str},
                "critical_news": [{"title": ..., "category": ..., "keywords": [...]}],
                "stock_sentiments": {"symbol": {"positive": N, "negative": N, "neutral": N, "tendency": str}},
            }
        """
        result: Dict = {
            "market_sentiment": {"positive": 0, "negative": 0, "neutral": 0, "total": 0, "tendency": "中性"},
            "critical_news": [],
            "stock_sentiments": {},
        }

        # 尝试加载 BERT 情感分析器
        analyzer = None
        try:
            from news_engine.sentiment import SentimentAnalyzer
            analyzer = SentimentAnalyzer.get_instance()
        except Exception as e:
            logger.warning(f"BERT 情感分析器加载失败，跳过情感分析: {e}")

        # ── 1. 市场新闻情感分析 + 重大新闻检测 ──
        market_news = data.get("market_overview", {}).get("web_search", {}).get("news", [])
        pos_count, neg_count, neu_count = 0, 0, 0

        for n in market_news:
            title = n.get("title", "")
            body = n.get("body", "")
            text = f"{title} {body}".strip()
            lang = n.get("lang", "zh")

            # BERT 情感分析
            if analyzer and text:
                try:
                    sa = analyzer.analyze(text, language=lang)
                    sentiment = sa["sentiment"]
                    n["sentiment"] = sentiment  # 回写到新闻数据
                    if sentiment == "positive":
                        pos_count += 1
                    elif sentiment == "negative":
                        neg_count += 1
                    else:
                        neu_count += 1
                except Exception:
                    n["sentiment"] = "neutral"
                    neu_count += 1
            else:
                neu_count += 1

            # 重大新闻关键词检测
            matched_kws = [kw for kw in self._CRITICAL_KEYWORDS if kw in title]
            if matched_kws:
                result["critical_news"].append({
                    "title": title,
                    "category": n.get("category", ""),
                    "source": n.get("source", ""),
                    "time": n.get("time", ""),
                    "keywords": matched_kws,
                    "sentiment": n.get("sentiment", "neutral"),
                })

        total = pos_count + neg_count + neu_count
        if total > 0:
            pos_pct = pos_count / total * 100
            neg_pct = neg_count / total * 100
            if pos_pct >= 50:
                tendency = "偏乐观"
            elif neg_pct >= 50:
                tendency = "偏悲观"
            elif pos_pct > neg_pct + 15:
                tendency = "中性偏乐观"
            elif neg_pct > pos_pct + 15:
                tendency = "中性偏谨慎"
            else:
                tendency = "中性"
        else:
            tendency = "中性"

        result["market_sentiment"] = {
            "positive": pos_count, "negative": neg_count, "neutral": neu_count,
            "total": total, "tendency": tendency,
        }

        # ── 2. 个股新闻情感分析（持仓股 + 推荐标的） ──
        if analyzer:
            stock_sentiments: Dict = {}

            # 持仓股新闻
            for pos in data.get("portfolio", {}).get("positions", []):
                sym = pos.get("symbol", "")
                news_list = pos.get("news", [])
                if sym and news_list:
                    stock_sentiments[sym] = self._analyze_stock_news(analyzer, news_list)

            # 推荐标的新闻
            for stock in data.get("top_stocks", {}).get("buy_recommendations", []):
                sym = stock.get("symbol", "")
                news_list = stock.get("news", [])
                if sym and news_list and sym not in stock_sentiments:
                    stock_sentiments[sym] = self._analyze_stock_news(analyzer, news_list)

            # 卖出预警新闻
            for stock in data.get("top_stocks", {}).get("sell_warnings", []):
                sym = stock.get("symbol", "")
                news_list = stock.get("news", [])
                if sym and news_list and sym not in stock_sentiments:
                    stock_sentiments[sym] = self._analyze_stock_news(analyzer, news_list)

            result["stock_sentiments"] = stock_sentiments

        logger.info(f"新闻情感分析: 市场新闻{total}条 "
                    f"(正面{pos_count}/负面{neg_count}/中性{neu_count} → {tendency}), "
                    f"重大新闻{len(result['critical_news'])}条, "
                    f"个股情感{len(result.get('stock_sentiments', {}))}只")

        return result

    @staticmethod
    def _analyze_stock_news(analyzer, news_list: List[Dict]) -> Dict:
        """对单只股票的新闻列表做情感汇总"""
        pos, neg, neu = 0, 0, 0
        for n in news_list:
            text = f"{n.get('title', '')} {n.get('content', '')}".strip()
            if not text:
                continue
            try:
                sa = analyzer.analyze(text, language="zh")
                sentiment = sa["sentiment"]
                n["sentiment"] = sentiment  # 回写
                if sentiment == "positive":
                    pos += 1
                elif sentiment == "negative":
                    neg += 1
                else:
                    neu += 1
            except Exception:
                n["sentiment"] = "neutral"
                neu += 1

        total = pos + neg + neu
        if total == 0:
            return {"positive": 0, "negative": 0, "neutral": 0, "tendency": "无数据"}

        if pos > neg:
            tendency = "偏正面"
        elif neg > pos:
            tendency = "偏负面"
        else:
            tendency = "中性"

        return {"positive": pos, "negative": neg, "neutral": neu, "tendency": tendency}
