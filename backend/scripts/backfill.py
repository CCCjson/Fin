"""
数据补全脚本 — 按顺序执行三个任务:

1. 补齐 109 只股票的日线行情缺口
2. 补跑 2025-02-06 ~ 2026-02-05 的信号检测
3. 更新信号追踪数据

用法:
    cd backend
    conda run -n quant python scripts/backfill.py
"""
import os
import sys

# 确保 backend/ 目录在 Python 路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
from datetime import datetime, timedelta
from collections import Counter
from loguru import logger
from sqlalchemy import func

# ── 配置 ──
BENCHMARK_SYMBOL = "000517.SZ"  # 基准股票（数据最全）
GAP_THRESHOLD = 20  # 缺口天数阈值
SIGNAL_LOOKBACK_DAYS = 400  # 信号检测回溯天数（覆盖一年缺口 + 指标计算需要的前置数据）


def task1_fix_quote_gaps():
    """任务 1: 补齐有缺口的股票日线数据（使用 EastMoneyCrawler + 代理 IP）"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import DailyQuote
    from eastmoney_crawler import EastMoneyCrawler, CrawlerConfig, parse_kline_data, ProxyTimeoutError
    from proxy_manager import ProxyManager

    logger.info("=" * 60)
    logger.info("任务 1: 补齐日线行情缺口（代理模式）")
    logger.info("=" * 60)

    session = get_session()

    # 获取基准交易日
    bench_dates = set(
        str(r[0]) for r in session.query(DailyQuote.date)
        .filter(DailyQuote.symbol == BENCHMARK_SYMBOL)
        .all()
    )
    total_trading_days = len(bench_dates)
    logger.info(f"基准股票 {BENCHMARK_SYMBOL}: {total_trading_days} 个交易日")

    # 找出有缺口的股票
    all_stocks = session.query(
        DailyQuote.symbol,
        func.count(DailyQuote.id).label("cnt"),
        func.min(DailyQuote.date).label("min_d"),
        func.max(DailyQuote.date).label("max_d"),
    ).group_by(DailyQuote.symbol).all()

    gap_stocks = []
    for sym, cnt, min_d, max_d in all_stocks:
        min_d_str = str(min_d)
        # 只检查 2024-06-01 之前就有数据的股票（排除新股）
        if min_d_str > "2024-06-01":
            continue
        # 算出这只股票应该有多少交易日
        expected = len([d for d in bench_dates if d >= min_d_str])
        missing = expected - cnt
        if missing > GAP_THRESHOLD:
            gap_stocks.append((sym, cnt, expected, missing, min_d_str, str(max_d)))

    if not gap_stocks:
        session.close()
        logger.success("没有发现需要补数据的股票")
        return

    gap_stocks.sort(key=lambda x: -x[3])  # 按缺失天数降序
    logger.info(f"发现 {len(gap_stocks)} 只股票有数据缺口")

    # ── 初始化代理 + 爬虫 ──
    proxy_mgr = ProxyManager()
    current_proxy = proxy_mgr.fetch_one_proxy()
    use_proxy = current_proxy is not None

    crawler = EastMoneyCrawler(CrawlerConfig(
        min_delay=0.3 if use_proxy else 2.0,
        max_delay=1.5 if use_proxy else 8.0,
        max_retries=0,      # 不重试
        retry_delay=0,
        timeout=8 if use_proxy else 30,
        rate_limit_pause=30.0 if use_proxy else 90.0,
    ))

    # 用代理预热获取 Cookie
    proxies = current_proxy.to_requests_proxies() if current_proxy else None
    crawler._warm_up(proxies=proxies)

    if current_proxy:
        logger.info(f"初始代理: {current_proxy.ip}:{current_proxy.port}")
    else:
        logger.warning("未获取到代理 IP，使用直连模式")

    proxy_switches = 0

    def get_proxies():
        nonlocal current_proxy, proxy_switches
        if current_proxy and not current_proxy.is_expired:
            return current_proxy.to_requests_proxies()
        # 代理过期，切换
        current_proxy = proxy_mgr.switch_proxy()
        proxy_switches += 1
        if current_proxy:
            logger.info(f"代理已过期，切换到: {current_proxy.ip}:{current_proxy.port}")
            return current_proxy.to_requests_proxies()
        return None

    def switch_on_error():
        nonlocal current_proxy, proxy_switches
        current_proxy = proxy_mgr.switch_proxy()
        proxy_switches += 1
        if current_proxy:
            logger.info(f"切换到新代理: {current_proxy.ip}:{current_proxy.port}")
        else:
            logger.warning("获取新代理失败，使用直连")
        new_proxies = current_proxy.to_requests_proxies() if current_proxy else None
        crawler.reset_session(proxies=new_proxies)

    success = 0
    failed = 0
    total_records = 0

    for i, (sym, cnt, expected, missing, min_d, max_d) in enumerate(gap_stocks):
        logger.info(f"[{i+1}/{len(gap_stocks)}] {sym}: 现有 {cnt} 天, 缺 {missing} 天, 范围 {min_d}~{max_d}")

        code = sym.split(".")[0]
        start_str = min_d.replace("-", "")
        end_str = datetime.now().strftime("%Y%m%d")

        try:
            data = crawler.fetch_stock_history(code, start_str, end_str, proxies=get_proxies())

            saved = 0
            if data:
                klines = parse_kline_data(data)
                for row in klines:
                    date_val = datetime.strptime(row["date"], "%Y-%m-%d").date()
                    existing = session.query(DailyQuote).filter(
                        DailyQuote.symbol == sym,
                        DailyQuote.date == date_val,
                    ).first()
                    if existing:
                        existing.open = row["open"]
                        existing.high = row["high"]
                        existing.low = row["low"]
                        existing.close = row["close"]
                        existing.volume = row["volume"]
                        existing.amount = row.get("amount")
                        existing.turnover = row.get("turnover")
                    else:
                        quote = DailyQuote(
                            symbol=sym,
                            market="a_share",
                            date=date_val,
                            open=row["open"],
                            high=row["high"],
                            low=row["low"],
                            close=row["close"],
                            volume=row["volume"],
                            amount=row.get("amount"),
                            turnover=row.get("turnover"),
                        )
                        session.add(quote)
                        saved += 1
                session.commit()
                total_records += saved

            logger.success(f"  补入 {saved} 条新记录")
            success += 1

        except ProxyTimeoutError as e:
            logger.warning(f"  被拦截，换 IP 跳过: {e}")
            failed += 1
            switch_on_error()

        except Exception as e:
            logger.error(f"  补数据失败: {e}")
            failed += 1

    session.close()
    proxy_mgr.print_status()
    logger.success(
        f"任务 1 完成: 成功 {success} 只, 失败 {failed} 只, "
        f"新记录 {total_records} 条, 换 IP {proxy_switches} 次"
    )


def task2_backfill_signals():
    """任务 2: 逐日补跑信号检测（滚动切片法）"""
    import pandas as pd
    from data_engine.storage.database import get_session
    from data_engine.storage.models import DailyQuote, Signal as SignalModel
    from data_engine.storage.history_repository import HistoryRepository
    from strategy.indicators import TechnicalIndicators
    from strategy.strategies import (
        MACrossStrategy, MACDStrategy, KDJStrategy, RSIStrategy
    )

    logger.info("=" * 60)
    logger.info("任务 2: 逐日补跑信号检测（滚动切片）")
    logger.info("=" * 60)

    session = get_session()
    repo = HistoryRepository()
    indicators = TechnicalIndicators()
    strategies = [
        MACrossStrategy(fast_period=5, slow_period=20),
        MACDStrategy(),
        KDJStrategy(),
        RSIStrategy(),
    ]

    # ── 1. 找出需要补跑的交易日 ──
    existing_signal_dates = set(
        str(r[0]) for r in session.query(SignalModel.date).distinct().all()
    )

    all_trading_dates = sorted(
        str(r[0]) for r in session.query(DailyQuote.date)
        .filter(DailyQuote.symbol == BENCHMARK_SYMBOL)
        .all()
    )

    # 缺口期间：有行情数据但没有信号的日期
    gap_dates = [d for d in all_trading_dates
                 if d not in existing_signal_dates and d >= "2025-02-06" and d <= "2026-02-05"]

    if not gap_dates:
        session.close()
        logger.success("没有需要补跑信号的日期")
        return

    logger.info(f"需要补跑 {len(gap_dates)} 个交易日: {gap_dates[0]} ~ {gap_dates[-1]}")

    # ── 2. 获取所有股票代码 ──
    symbols = [r[0] for r in session.query(DailyQuote.symbol).distinct().all()]
    logger.info(f"共 {len(symbols)} 只股票")

    # 指标计算需要的前置数据天数（MA60 需要约 60 天）
    INDICATOR_WARMUP = 120

    total_signals = 0
    total_buy = 0
    total_sell = 0
    failed_stocks = 0

    # ── 3. 逐只股票处理（每只加载一次数据，扫全部缺口日期） ──
    for si, symbol in enumerate(symbols):
        try:
            # 加载该股票在缺口窗口的全部日线数据（含前置 warmup）
            rows = session.query(DailyQuote).filter(
                DailyQuote.symbol == symbol,
            ).order_by(DailyQuote.date.asc()).all()

            if len(rows) < INDICATOR_WARMUP:
                continue

            # 转为 DataFrame
            df_full = pd.DataFrame([{
                "date": r.date,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            } for r in rows])
            df_full["date"] = pd.to_datetime(df_full["date"])

            # 计算指标（在完整数据上算一次）
            df_full = indicators.calculate_all_indicators(df_full)

            # 建立日期 → 行索引映射，方便快速切片
            date_str_list = df_full["date"].dt.strftime("%Y-%m-%d").tolist()
            date_to_idx = {d: i for i, d in enumerate(date_str_list)}

            stock_signals = 0
            for gap_date in gap_dates:
                idx = date_to_idx.get(gap_date)
                if idx is None or idx < 2:
                    continue  # 这只股票在该日无数据

                # 切片到当天（策略会用 iloc[-1] 和 iloc[-2]）
                df_slice = df_full.iloc[:idx + 1]

                for strategy in strategies:
                    try:
                        signals = strategy.generate_signals(df_slice, symbol)
                        for sig in signals:
                            saved = repo.save_signal(**sig.to_dict())
                            if saved:
                                stock_signals += 1
                                total_signals += 1
                                if sig.signal_type.upper() == "BUY":
                                    total_buy += 1
                                else:
                                    total_sell += 1
                    except Exception:
                        pass  # 单策略单日失败不影响其他

        except Exception as e:
            logger.debug(f"{symbol} 处理失败: {e}")
            failed_stocks += 1

        # 进度日志（每 1% 或每 50 只打印一次）
        if (si + 1) % max(1, len(symbols) // 100) == 0 or si + 1 == len(symbols):
            pct = (si + 1) / len(symbols) * 100
            logger.info(
                f"[{si+1}/{len(symbols)}] {pct:.0f}% | "
                f"信号: {total_signals} (买{total_buy} 卖{total_sell}) 失败: {failed_stocks}"
            )

    session.close()
    logger.success(
        f"任务 2 完成: 扫描 {len(symbols)} 只股票 × {len(gap_dates)} 个交易日, "
        f"信号 {total_signals} 条 (买 {total_buy} / 卖 {total_sell}), "
        f"失败 {failed_stocks} 只"
    )


def task3_update_tracking():
    """任务 3: 更新信号追踪数据"""
    from analysis_engine.signal_tracker import SignalTracker

    logger.info("=" * 60)
    logger.info("任务 3: 更新信号追踪数据")
    logger.info("=" * 60)

    tracker = SignalTracker()
    result = tracker.update_all()
    tracker.close()

    logger.success(
        f"任务 3 完成: 新建 {result['created']} 条, "
        f"更新 {result['updated']} 条, "
        f"完成 {result['completed']} 条"
    )


if __name__ == "__main__":
    start = time.time()
    logger.info("开始执行数据补全脚本...")

    task1_fix_quote_gaps()
    task2_backfill_signals()
    task3_update_tracking()

    elapsed = time.time() - start
    logger.success(f"全部完成! 总耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
