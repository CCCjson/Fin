"""
信号回补脚本 — 补齐 2023-01-01 ~ 2025-02-04 的信号数据

采用「逐只股票 × 滚动切片」策略：
  - 每只股票只加载一次完整日线数据 + 计算一次技术指标
  - 在完整 DataFrame 上逐日切片运行策略（避免重复计算）
  - 内置去重（save_signal 会自动跳过已有信号）

用法:
    cd backend
    conda run -n quant python scripts/backfill_signals_2023.py

支持中断重跑：已保存的信号不会重复写入，脚本会自动跳过。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from loguru import logger
from sqlalchemy import func, distinct

from data_engine.storage.database import get_session
from data_engine.storage.models import DailyQuote, Signal as SignalModel
from data_engine.storage.history_repository import HistoryRepository
from strategy.indicators import TechnicalIndicators
from strategy.strategies import (
    MACrossStrategy, MACDStrategy, KDJStrategy, RSIStrategy
)

# ── 配置 ──
BACKFILL_START = "2023-01-01"
BACKFILL_END = "2025-02-04"
INDICATOR_WARMUP = 120  # 指标计算需要的前置天数（MA60 等需要约 60 天数据）


def get_gap_dates(session) -> list:
    """找出回补区间内有行情数据但没有信号的交易日"""
    # 回补区间内所有有行情的交易日
    quote_dates = set(
        str(r[0]) for r in session.query(distinct(DailyQuote.date))
        .filter(DailyQuote.date >= BACKFILL_START, DailyQuote.date <= BACKFILL_END)
        .all()
    )

    # 回补区间内已有信号的日期
    signal_dates = set(
        str(r[0]) for r in session.query(distinct(SignalModel.date))
        .filter(SignalModel.date >= BACKFILL_START, SignalModel.date <= BACKFILL_END)
        .all()
    )

    # 有行情但无信号的日期
    gap = sorted(quote_dates - signal_dates)
    logger.info(
        f"回补区间 {BACKFILL_START} ~ {BACKFILL_END}: "
        f"行情日 {len(quote_dates)} 天, 已有信号 {len(signal_dates)} 天, "
        f"待补 {len(gap)} 天"
    )
    return gap


def backfill():
    session = get_session()
    repo = HistoryRepository()
    indicators = TechnicalIndicators()
    strategies = [
        MACrossStrategy(fast_period=5, slow_period=20),
        MACDStrategy(),
        KDJStrategy(),
        RSIStrategy(),
    ]

    # 1. 确定需要补的日期
    gap_dates = get_gap_dates(session)
    if not gap_dates:
        logger.success("无需回补，所有交易日均已有信号！")
        session.close()
        return

    gap_set = set(gap_dates)
    logger.info(f"需回补: {gap_dates[0]} ~ {gap_dates[-1]} ({len(gap_dates)} 天)")

    # 2. 获取所有股票
    symbols = [r[0] for r in session.query(distinct(DailyQuote.symbol)).all()]
    logger.info(f"共 {len(symbols)} 只股票")

    total_signals = 0
    total_buy = 0
    total_sell = 0
    failed_stocks = 0
    start_time = time.time()

    # 3. 逐只股票处理
    for si, symbol in enumerate(symbols):
        try:
            # 加载完整日线数据（需要包含 warmup 区间）
            rows = session.query(DailyQuote).filter(
                DailyQuote.symbol == symbol,
            ).order_by(DailyQuote.date.asc()).all()

            if len(rows) < INDICATOR_WARMUP:
                continue

            # 转 DataFrame
            df_full = pd.DataFrame([{
                "date": r.date,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            } for r in rows])
            df_full["date"] = pd.to_datetime(df_full["date"])

            # 一次性计算所有技术指标
            df_full = indicators.calculate_all_indicators(df_full)

            # 日期 → 行索引映射
            date_str_list = df_full["date"].dt.strftime("%Y-%m-%d").tolist()
            date_to_idx = {d: i for i, d in enumerate(date_str_list)}

            stock_signals = 0
            for gap_date in gap_dates:
                idx = date_to_idx.get(gap_date)
                if idx is None or idx < 2:
                    continue

                # 切片到当天
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
                        pass

        except Exception as e:
            logger.debug(f"{symbol} 处理失败: {e}")
            failed_stocks += 1

        # 进度日志（每 1% 打印一次）
        if (si + 1) % max(1, len(symbols) // 100) == 0 or si + 1 == len(symbols):
            pct = (si + 1) / len(symbols) * 100
            elapsed = time.time() - start_time
            speed = (si + 1) / elapsed if elapsed > 0 else 0
            remaining = (len(symbols) - si - 1) / speed if speed > 0 else 0
            logger.info(
                f"[{si+1}/{len(symbols)}] {pct:.0f}% | "
                f"信号: {total_signals} (买{total_buy} 卖{total_sell}) | "
                f"失败: {failed_stocks} | "
                f"速度: {speed:.1f} 只/s | "
                f"剩余: {remaining/60:.0f} min"
            )

    session.close()
    repo.close()
    elapsed = time.time() - start_time

    logger.success(
        f"信号回补完成！\n"
        f"  区间: {BACKFILL_START} ~ {BACKFILL_END} ({len(gap_dates)} 天)\n"
        f"  股票: {len(symbols)} 只 (失败 {failed_stocks})\n"
        f"  信号: {total_signals} 条 (买 {total_buy} / 卖 {total_sell})\n"
        f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f} min)"
    )


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("信号回补脚本 — 2023-01-01 ~ 2025-02-04")
    logger.info("=" * 60)
    backfill()
