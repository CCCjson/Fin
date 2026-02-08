"""
全量 A股数据拉取脚本

使用企业级爬虫框架，具备：
- 请求限速 + 指数退避重试
- 随机 UA 和浏览器指纹
- Session/Cookie 自动维护
- 自适应限流检测
"""

import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import akshare as ak
import pandas as pd
from loguru import logger

from data_engine.storage.database import init_db, get_session
from data_engine.storage.models import StockInfo, DailyQuote, DataUpdateLog

# 导入企业级爬虫
from eastmoney_crawler import EastMoneyCrawler, CrawlerConfig, parse_kline_data


# ============ 配置 ============
START_DATE = "2023-01-01"
END_DATE = "2025-02-05"
BATCH_SIZE = 500  # 每批保存进度
LONG_PAUSE_EVERY = 500  # 每处理多少只后长休息
LONG_PAUSE_TIME = 120   # 长休息时间（秒）

# 进度文件路径
PROGRESS_FILE = Path(__file__).parent / "fetch_progress.json"

# 日志配置
log_dir = Path(__file__).parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

logger.remove()
logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <level>{message}</level>"
)
logger.add(log_dir / "fetch_all_a_shares.log", level="DEBUG", rotation="10 MB")


def load_progress() -> Dict:
    """加载进度"""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "failed": [], "last_index": 0}


def save_progress(progress: Dict):
    """保存进度"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def get_all_a_share_list() -> List[Dict]:
    """获取所有 A股股票列表"""
    logger.info("正在获取 A股股票列表...")

    try:
        df = ak.stock_info_a_code_name()

        stocks = []
        for _, row in df.iterrows():
            code = row["code"]
            if code.startswith("6"):
                market_suffix = "SH"
            elif code.startswith(("0", "3")):
                market_suffix = "SZ"
            else:
                continue

            stocks.append({
                "symbol": f"{code}.{market_suffix}",
                "name": row["name"],
                "code": code
            })

        logger.info(f"获取到 {len(stocks)} 只 A股")
        return stocks

    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        raise


def save_stock_list_to_db(stocks: List[Dict], session):
    """保存股票列表到数据库"""
    logger.info("正在保存股票列表到数据库...")

    saved_count = 0
    for stock in stocks:
        try:
            existing = session.query(StockInfo).filter(
                StockInfo.symbol == stock["symbol"]
            ).first()

            if existing:
                existing.name = stock["name"]
                existing.is_active = 1
            else:
                stock_info = StockInfo(
                    symbol=stock["symbol"],
                    name=stock["name"],
                    market="a_share",
                    is_active=1
                )
                session.add(stock_info)
                saved_count += 1
        except Exception as e:
            logger.warning(f"保存股票 {stock['symbol']} 失败: {e}")

    session.commit()
    logger.info(f"股票列表保存完成，新增 {saved_count} 只")


def save_daily_quotes_to_db(symbol: str, klines: List[Dict], session) -> int:
    """保存日线数据到数据库"""
    if not klines:
        return 0

    saved_count = 0

    for row in klines:
        try:
            date_val = datetime.strptime(row["date"], "%Y-%m-%d").date()

            existing = session.query(DailyQuote).filter(
                DailyQuote.symbol == symbol,
                DailyQuote.date == date_val
            ).first()

            if existing:
                existing.open = row["open"]
                existing.high = row["high"]
                existing.low = row["low"]
                existing.close = row["close"]
                existing.volume = row["volume"]
                existing.amount = row["amount"]
                existing.turnover = row["turnover"]
            else:
                quote = DailyQuote(
                    symbol=symbol,
                    market="a_share",
                    date=date_val,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    amount=row["amount"],
                    turnover=row["turnover"]
                )
                session.add(quote)
                saved_count += 1

        except Exception as e:
            logger.warning(f"保存 {symbol} {row.get('date')} 失败: {e}")

    session.commit()
    return saved_count


def main():
    """主函数"""
    start_time = datetime.now()

    logger.info("=" * 60)
    logger.info("全量 A股数据拉取 - 企业级爬虫版")
    logger.info("=" * 60)
    logger.info(f"时间范围: {START_DATE} ~ {END_DATE}")
    logger.info(f"长休息: 每 {LONG_PAUSE_EVERY} 只休息 {LONG_PAUSE_TIME} 秒")
    logger.info("=" * 60)

    # 初始化数据库
    init_db()
    session = get_session()

    # 初始化爬虫
    crawler = EastMoneyCrawler(CrawlerConfig(
        min_delay=2.0,      # 最小间隔 2 秒
        max_delay=8.0,      # 最大间隔 8 秒
        max_retries=3,      # 最多重试 3 次
        retry_delay=15.0,   # 重试延迟 15 秒
        timeout=30,         # 超时 30 秒
        rate_limit_pause=90.0,  # 被限流暂停 90 秒
    ))
    logger.info("爬虫初始化完成")

    # 加载进度
    progress = load_progress()
    completed_symbols = set(progress["completed"])
    failed_symbols = []

    logger.info(f"已完成: {len(completed_symbols)} 只")

    # 获取股票列表
    stocks = get_all_a_share_list()

    # 保存股票列表到数据库
    save_stock_list_to_db(stocks, session)

    # 统计
    total = len(stocks)
    success_count = 0
    fail_count = 0
    skip_count = 0
    total_records = 0

    # 格式化日期
    start_date_fmt = START_DATE.replace("-", "")
    end_date_fmt = END_DATE.replace("-", "")

    logger.info(f"\n开始拉取日线数据，共 {total} 只股票...\n")

    try:
        for i, stock in enumerate(stocks):
            symbol = stock["symbol"]
            code = stock["code"]
            name = stock["name"]

            # 跳过已完成的
            if symbol in completed_symbols:
                skip_count += 1
                continue

            # 进度显示
            progress_pct = (i + 1) / total * 100
            logger.info(f"[{i+1}/{total}] ({progress_pct:.1f}%) {symbol} {name}")

            # 使用爬虫获取数据
            data = crawler.fetch_stock_history(code, start_date_fmt, end_date_fmt)

            if data:
                klines = parse_kline_data(data)
                if klines:
                    saved = save_daily_quotes_to_db(symbol, klines, session)
                    total_records += saved
                    success_count += 1
                    completed_symbols.add(symbol)
                    logger.info(f"  ✓ 成功: {len(klines)} 条数据, 新增 {saved} 条")
                else:
                    # 有响应但无数据（可能是新股）
                    completed_symbols.add(symbol)
                    logger.info(f"  ○ 无数据 (可能是新股或停牌)")
            else:
                fail_count += 1
                failed_symbols.append(symbol)
                logger.warning(f"  ✗ 失败")

            # 定期保存进度
            if (i + 1) % BATCH_SIZE == 0:
                progress["completed"] = list(completed_symbols)
                progress["failed"] = failed_symbols
                progress["last_index"] = i
                save_progress(progress)

                # 显示爬虫统计
                stats = crawler.get_stats()
                logger.info(f"  [进度已保存] 爬虫统计: 成功率={stats['success_rate']}, 当前延迟={stats['current_delay']}")

            # 长休息
            if success_count > 0 and success_count % LONG_PAUSE_EVERY == 0:
                logger.info(f"  [已处理 {success_count} 只，休息 {LONG_PAUSE_TIME} 秒，重置 Session...]")
                crawler.reset_session()
                time.sleep(LONG_PAUSE_TIME)

    except KeyboardInterrupt:
        logger.warning("\n用户中断，正在保存进度...")
        progress["completed"] = list(completed_symbols)
        progress["failed"] = failed_symbols
        save_progress(progress)
        logger.info("进度已保存，下次运行将继续。")
        session.close()
        return

    # 最终保存进度
    progress["completed"] = list(completed_symbols)
    progress["failed"] = failed_symbols
    save_progress(progress)

    # 记录日志
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    log = DataUpdateLog(
        market="a_share",
        update_type="daily",
        symbols_count=success_count,
        records_count=total_records,
        status="success" if fail_count == 0 else "partial",
        started_at=start_time,
        completed_at=end_time,
        duration_seconds=duration
    )
    session.add(log)
    session.commit()

    # 打印总结
    stats = crawler.get_stats()

    logger.info("\n" + "=" * 60)
    logger.info("数据拉取完成!")
    logger.info("=" * 60)
    logger.info(f"总股票数: {total}")
    logger.info(f"成功: {success_count}")
    logger.info(f"失败: {fail_count}")
    logger.info(f"跳过: {skip_count}")
    logger.info(f"总记录数: {total_records}")
    logger.info(f"耗时: {duration/60:.1f} 分钟")
    logger.info("-" * 60)
    logger.info(f"爬虫统计:")
    logger.info(f"  总请求: {stats['total_requests']}")
    logger.info(f"  成功率: {stats['success_rate']}")
    logger.info(f"  被限流: {stats['rate_limited']} 次")
    logger.info("=" * 60)

    if failed_symbols:
        logger.warning(f"\n失败的股票 ({len(failed_symbols)} 只):")
        for sym in failed_symbols[:20]:
            logger.warning(f"  - {sym}")
        if len(failed_symbols) > 20:
            logger.warning(f"  ... 等 {len(failed_symbols) - 20} 只")

    session.close()


if __name__ == "__main__":
    main()
